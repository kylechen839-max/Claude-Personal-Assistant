"""A student's deadlines (exams, assignments, quizzes, classes), captured
from Canvas, Gmail notifications, screenshots or by hand, kept in one SQLite
file per person: the owner's `<[student] dir>/deadlines.sqlite`, an invited
person's `<[family] dir>/<id>/deadlines.sqlite` (deleted with their folder).

The API other modules use (`uid` None is the owner, else a family id):

  store = open_store(config)           # the process's store (one per config)
  store.list(uid)                      # what is known now, as dicts:
      {id, course, title, kind, due, status, source, source_due, captured_at}
      kind: exam | assignment | quiz | class
      due: "YYYY-MM-DD" (a whole day) or "YYYY-MM-DDTHH:MM:SSZ" (UTC); a
           floating time without a zone is "YYYY-MM-DDTHH:MM:SS"
      status: draft (captured, not checked) | confirmed (the person said so)
              | conflict (two sources disagree on the date)
      source: ics | canvas_api | gmail | screenshot | manual | modules
      source_due: what the source last said (differs from `due` once the
           person confirmed another date)
      captured_at: "YYYY-MM-DDTHH:MM:SSZ", when this row was first captured
  store.propose(uid, candidates, source)  # a sync's findings (idempotent)
  store.resolve(uid, id, choice)       # "confirm", "dismiss" or a due date
  store.add(uid, title, due, course, kind)   # said by the person: confirmed
  store.delete(uid, id) / store.delete_source(uid, source)
  store.export(uid)                    # every row (account export)

Rules:
- One row per (source, source_key): re-syncing the same feed changes
  nothing, and a changed date in the feed updates a draft.
- The same item (course code + title) from two sources with different
  dates is a conflict on both rows until the person picks one. A title
  one source repeats under other keys (a weekly lecture, a lab) is several
  items: they never conflict with each other or with another source, and
  confirming one dismisses nothing. Several emails about one item don't
  conflict with each other: the newest by Gmail's own arrival time
  (`sent_at`) speaks for them, and older ones are "superseded" (hidden
  from list(), back if the newest is deleted). A confirmed email date
  always stays the anchor, so a newer email that disagrees is a conflict.
- A confirmed date is never overwritten by a sync: the row keeps it as
  `due` and only `source_due` follows the feed. Confirming one side of a
  conflict dismisses the other rows of that item.
- A dismissed row stays dismissed through later syncs. A deleted synced
  row leaves only a hash of its source key (a tombstone), so the next sync
  doesn't bring it back; deleting the whole source clears those too.
- Deleted means gone: secure_delete=ON overwrites the deleted bytes, and a
  TRUNCATE checkpoint empties the write-ahead log after every delete.
- Bounded: [student] max_items per person, max_per_source per source;
  rows whose date is more than PRUNE_DAYS past are dropped on each sync.

Everything here is data from outside (a Canvas feed, an email, a
screenshot): callers mark it untrusted (tools/deadlines.py). Logs say
ids, counts and source names only, never a title or course.
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
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Callable, Iterable

log = logging.getLogger(__name__)

FILE = "deadlines.sqlite"
KINDS = ("exam", "assignment", "quiz", "class")
SOURCES = ("ics", "canvas_api", "gmail", "screenshot", "manual", "modules")
STATUSES = ("draft", "confirmed", "conflict", "dismissed",
            "superseded")  # an older email behind a newer one: hidden, not gone
# which copy list() shows when two sources agree (most trusted first)
SOURCE_RANK = {"manual": 0, "canvas_api": 1, "ics": 2, "modules": 3, "gmail": 4,
               "screenshot": 5}
MAX_TITLE = 200
MAX_COURSE = 100
MAX_KEY = 200
MAX_TOMBSTONES = 5000     # per person
MAX_SEEN = 2000           # per person and source (emails, pages already read)
PRUNE_DAYS = 60           # a row due longer ago than this is dropped on sync
TIMED_SLACK = 60          # seconds: two times this close are the same due
_COURSE_CODE = re.compile(r"\b([A-Za-z]{2,5})\s*-?\s*(\d{2,4}[A-Za-z]?)\b")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_key TEXT NOT NULL,
    item_key TEXT NOT NULL,
    course TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    kind TEXT NOT NULL,
    due TEXT NOT NULL,
    source_due TEXT NOT NULL,
    status TEXT NOT NULL,
    confirmed_value TEXT,
    confirmed_at REAL,
    captured_at REAL NOT NULL,
    sent_at REAL,
    UNIQUE (source, source_key)
);
CREATE INDEX IF NOT EXISTS items_item ON items (item_key);
CREATE TABLE IF NOT EXISTS tombstones (
    source TEXT NOT NULL, key_hash TEXT NOT NULL, at REAL NOT NULL,
    PRIMARY KEY (source, key_hash));
CREATE TABLE IF NOT EXISTS seen (
    source TEXT NOT NULL, key_hash TEXT NOT NULL, at REAL NOT NULL,
    PRIMARY KEY (source, key_hash));
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class DeadlineError(ValueError):
    """A request that can't be done (bad id, bad date); the message is
    safe to show (it never quotes a title)."""


def _squash(text, limit: int) -> str:
    text = re.sub(r"\s+", " ", "".join(c for c in str(text or "") if c.isprintable()))
    return text.strip()[:limit]


def _norm(text: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", str(text or "").lower())


def course_code(course: str) -> str:
    """"MATH 241 - Fall 2026" and "math241" alike -> "math241"."""
    found = _COURSE_CODE.search(course or "")
    return (found.group(1) + found.group(2)).lower() if found else _norm(course)


def item_key(course: str, title: str) -> str:
    return f"{course_code(course)}|{_norm(title)}"


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_due(value, tz: tzinfo | None = None) -> str:
    """A due date as stored: "YYYY-MM-DD", a UTC "...Z" time, or (no zone
    given and none in the value) a floating "YYYY-MM-DDTHH:MM:SS". Raises
    DeadlineError for anything else."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = None
        day = value
    else:
        text = str(value or "").strip()
        if _DATE.match(text):
            try:
                day = date.fromisoformat(text)
            except ValueError:
                raise DeadlineError("that isn't a date") from None
            dt = None
        else:
            try:
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                raise DeadlineError("give the date as YYYY-MM-DD or "
                                    "YYYY-MM-DDTHH:MM") from None
    if dt is None:
        if not 2000 <= day.year <= 2100:
            raise DeadlineError("that date is out of range")
        return day.isoformat()
    if not 2000 <= dt.year <= 2100:
        raise DeadlineError("that date is out of range")
    if dt.tzinfo is None and tz is not None:
        dt = dt.replace(tzinfo=tz)
    if dt.tzinfo is None:
        return dt.replace(microsecond=0).isoformat()
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(due: str) -> date | datetime:
    if _DATE.match(due):
        return date.fromisoformat(due)
    dt = datetime.fromisoformat(due.replace("Z", "+00:00"))
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def same_due(a: str, b: str) -> bool:
    """Two dues for the same thing: equal days, times within TIMED_SLACK,
    or a whole day and a time within a day of it (a deadline at 23:59 in
    the US is the next day in UTC)."""
    if a == b:
        return True
    try:
        x, y = _parse(a), _parse(b)
    except ValueError:
        return False
    if isinstance(x, datetime) and isinstance(y, datetime):
        return abs((x - y).total_seconds()) <= TIMED_SLACK
    if not isinstance(x, datetime) and not isinstance(y, datetime):
        return x == y
    day, when = (x, y) if isinstance(y, datetime) else (y, x)
    return abs((when.astimezone(timezone.utc).date() - day).days) <= 1


def due_moment(due: str) -> datetime:
    """A due as an aware datetime (a whole day: its end, 23:59 UTC) for
    sorting and windows."""
    value = _parse(due)
    if isinstance(value, datetime):
        return value
    return datetime(value.year, value.month, value.day, 23, 59, tzinfo=timezone.utc)


def clean_candidate(candidate: dict, tz: tzinfo | None = None) -> dict:
    """{source_key, course, title, kind, due, sent_at?} trimmed and checked
    (sent_at: when the source wrote it, epoch seconds, e.g. an email's
    Gmail arrival time); raises DeadlineError when the title, key or date
    is missing or bad."""
    if not isinstance(candidate, dict):
        raise DeadlineError("not a deadline")
    title = _squash(candidate.get("title"), MAX_TITLE)
    key = _squash(candidate.get("source_key"), MAX_KEY)
    if not title or not key:
        raise DeadlineError("a deadline needs a title")
    kind = str(candidate.get("kind") or "assignment").lower()
    sent = candidate.get("sent_at")  # the source's own time (Gmail's arrival time)
    sent = float(sent) if isinstance(sent, (int, float)) and not isinstance(sent, bool) \
        and sent > 0 else None
    return {"source_key": key, "title": title,
            "course": _squash(candidate.get("course"), MAX_COURSE),
            "kind": kind if kind in KINDS else "assignment",
            "due": normalize_due(candidate.get("due"), tz), "sent_at": sent}


class DeadlineStore:
    """Each person's deadlines (see the module docstring). Thread-safe:
    one lock around every read and write; each call opens its own short
    connection, so nothing holds a person's file open (their folder can be
    deleted on Windows)."""

    def __init__(self, owner_dir: str | Path, family_dir: str | Path,
                 max_items: int = 2000, max_per_source: int = 500,
                 clock: Callable[[], float] = time.time):
        self.owner_dir = Path(owner_dir)
        self.family_dir = Path(family_dir)
        self.max_items = int(max_items)
        self.max_per_source = int(max_per_source)
        self.clock = clock
        self.config: dict | None = None   # set by open_store
        self.lock = threading.RLock()
        # Family.while_listed: a context manager yielding whether `uid` is
        # still listed, holding Family's lock, so a write never brings back
        # the folder of someone just removed. None: no family accounts.
        self.member_guard: Callable[[str], object] | None = None

    @classmethod
    def from_config(cls, config: dict, clock=time.time) -> "DeadlineStore":
        s = config["student"]
        return cls(s["dir"], config["family"]["dir"], max_items=s["max_items"],
                   max_per_source=s["max_per_source"], clock=clock)

    # ---------- files ----------
    def path(self, uid: str | None) -> Path:
        if uid is None:
            return self.owner_dir / FILE
        from kwil.family import valid_id

        if not valid_id(uid):
            raise DeadlineError("not a person")
        return self.family_dir / uid / FILE

    @contextmanager
    def _db(self, uid: str | None, write: bool):
        """A connection to `uid`'s file, or None when reading a file that
        doesn't exist (a read never creates one). A write for someone no
        longer listed raises KeyError and writes nothing."""
        path = self.path(uid)
        guard = (self.member_guard(uid) if uid is not None and write
                 and self.member_guard is not None else nullcontext(True))
        with guard as listed, self.lock:
            if not listed:
                raise KeyError("no longer listed")
            if not write and not path.exists():
                yield None
                return
            if write:
                path.parent.mkdir(parents=True, exist_ok=True)
            con = sqlite3.connect(str(path), isolation_level=None, timeout=10)
            try:
                con.row_factory = sqlite3.Row
                con.execute("PRAGMA journal_mode = WAL")
                # deleted rows are overwritten with zeros, not just unlinked
                con.execute("PRAGMA secure_delete = ON")
                con.executescript(SCHEMA)
                if "sent_at" not in {r["name"] for r in con.execute(
                        "PRAGMA table_info(items)")}:  # a file from round 1's schema
                    con.execute("ALTER TABLE items ADD COLUMN sent_at REAL")
                yield con
            finally:
                con.close()

    @staticmethod
    def _purge(con) -> None:
        """After a delete: the old bytes out of the write-ahead log too."""
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    # ---------- reading ----------
    @staticmethod
    def _public(row) -> dict:
        return {"id": row["id"], "course": row["course"], "title": row["title"],
                "kind": row["kind"], "due": row["due"], "status": row["status"],
                "source": row["source"], "source_due": row["source_due"],
                "captured_at": _iso(row["captured_at"])}

    def list(self, uid: str | None, include_dismissed: bool = False) -> list[dict]:
        """What is known, soonest first. Copies of one item that agree on
        the date (two sources) are shown once, the most trusted copy;
        conflicting copies are all shown (status "conflict")."""
        with self._db(uid, write=False) as con:
            if con is None:
                return []
            rows = con.execute("SELECT * FROM items").fetchall()
        shown, groups = [], {}
        for row in rows:
            if row["status"] in ("dismissed", "superseded"):
                if include_dismissed:
                    shown.append(row)
                continue
            groups.setdefault(row["item_key"], []).append(row)
        for group in groups.values():
            group.sort(key=lambda r: (r["status"] != "confirmed",
                                      SOURCE_RANK.get(r["source"], 9), r["id"]))
            kept: list = []
            for row in group:
                # the same item from two sources (or two emails about it);
                # one source's rows with the same title are separate items
                if not any(same_due(row["due"], k["due"])
                           and (k["source"] != row["source"] or row["source"] == "gmail")
                           for k in kept):
                    kept.append(row)
            shown.extend(kept)
        shown.sort(key=lambda r: (due_moment(r["due"]), r["id"]))
        return [self._public(r) for r in shown]

    def get(self, uid: str | None, item_id: int) -> dict | None:
        with self._db(uid, write=False) as con:
            if con is None:
                return None
            row = con.execute("SELECT * FROM items WHERE id = ?",
                              (int(item_id),)).fetchone()
        return self._public(row) if row is not None else None

    def export(self, uid: str | None) -> list[dict]:
        """Every row, dismissed ones too, for "Download my data"."""
        with self._db(uid, write=False) as con:
            if con is None:
                return []
            rows = con.execute("SELECT * FROM items ORDER BY id").fetchall()
        return [{**self._public(r), "confirmed_value": r["confirmed_value"]} for r in rows]

    def last_sync(self, uid: str | None) -> str | None:
        with self._db(uid, write=False) as con:
            if con is None:
                return None
            row = con.execute("SELECT value FROM meta WHERE key = 'last_sync'").fetchone()
        return row["value"] if row is not None else None

    # ---------- writing ----------
    @staticmethod
    def _sent_order(row) -> tuple:
        """Which email is newer: Gmail's own arrival time (set when it
        arrived; an email can't change it), else when it was captured, and
        within one sync the earlier row (Gmail lists newest first)."""
        sent = row["sent_at"]
        return (sent if sent is not None else row["captured_at"], row["captured_at"],
                -row["id"])

    @classmethod
    def _singles(cls, rows) -> set:
        """Ids of the rows that take part in an item's date: each source's
        only copy of it, the newest email about it, and every confirmed
        email (the person's answer is always an anchor). A title one
        source repeats under other keys (a weekly lecture, a lab each
        Tuesday) is several items, never a conflict."""
        mail = [r for r in rows if r["source"] == "gmail"]
        newest = max(mail, key=cls._sent_order)["id"] if mail else None
        count: dict = {}
        for r in rows:
            if r["source"] != "gmail":
                count[r["source"]] = count.get(r["source"], 0) + 1
        return {r["id"] for r in rows if r["id"] == newest
                or (r["source"] == "gmail" and r["status"] == "confirmed")
                or (r["source"] != "gmail" and count[r["source"]] == 1)}

    def _recompute(self, con, key: str) -> None:
        """The statuses of one item's rows (see the module docstring)."""
        rows = con.execute("SELECT * FROM items WHERE item_key = ? AND status != "
                           "'dismissed' ORDER BY id", (key,)).fetchall()
        if not rows:
            return
        status = {}
        singles = self._singles(rows)
        for r in rows:
            if r["id"] in singles or r["status"] == "confirmed":
                continue
            # an older email: hidden behind the newest, never dismissed (it
            # comes back if the newest one is deleted); repeats of one
            # source's title: separate items
            status[r["id"]] = "superseded" if r["source"] == "gmail" else "draft"
        rows = [r for r in rows if r["id"] in singles]
        confirmed = [r for r in rows if r["status"] == "confirmed"]
        if confirmed:
            anchor = max(confirmed, key=lambda r: (r["confirmed_at"] or 0, r["id"]))
            for r in rows:
                if r["id"] == anchor["id"]:
                    continue
                agrees = same_due(r["due"], anchor["due"])
                if r["status"] == "confirmed":
                    status[r["id"]] = "confirmed" if agrees else "dismissed"
                else:
                    status[r["id"]] = "draft" if agrees else "conflict"
        elif rows:
            split = any(not same_due(r["due"], rows[0]["due"]) for r in rows[1:])
            status.update({r["id"]: "conflict" if split else "draft" for r in rows})
        for rid, new in status.items():
            con.execute("UPDATE items SET status = ? WHERE id = ?", (new, rid))

    def _cutoff(self) -> datetime:
        return datetime.fromtimestamp(self.clock(), timezone.utc) - timedelta(days=PRUNE_DAYS)

    def _prune(self, con) -> int:
        cutoff = self._cutoff()
        old = [r["id"] for r in con.execute("SELECT id, due FROM items").fetchall()
               if due_moment(r["due"]) < cutoff]
        for rid in old:
            con.execute("DELETE FROM items WHERE id = ?", (rid,))
        return len(old)

    def propose(self, uid: str | None, candidates: Iterable[dict], source: str,
                tz: tzinfo | None = None) -> dict:
        """Upsert a sync's findings ({source_key, course, title, kind, due}
        each) from `source`. Returns counts {added, updated, unchanged,
        skipped, conflicts}; conflicts is how many of the person's rows are
        in conflict afterwards. Bad candidates, long-past ones, tombstoned
        keys and anything over the bounds are skipped (counted). Naive
        times are read in `tz`. KeyError: `uid` was removed."""
        if source not in SOURCES:
            raise DeadlineError("unknown source")
        counts = {"added": 0, "updated": 0, "unchanged": 0, "skipped": 0, "conflicts": 0}
        clean = []
        for candidate in candidates:
            try:
                clean.append(clean_candidate(candidate, tz))
            except DeadlineError:
                counts["skipped"] += 1
        now = self.clock()
        cutoff = self._cutoff()
        with self._db(uid, write=True) as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                pruned = self._prune(con)
                touched: set[str] = set()
                for c in clean:
                    if due_moment(c["due"]) < cutoff:  # long past: pruned anyway
                        counts["skipped"] += 1
                        continue
                    touched |= self._upsert(con, source, c, now, counts)
                for key in touched:
                    self._recompute(con, key)
                counts["conflicts"] = con.execute(
                    "SELECT COUNT(*) FROM items WHERE status = 'conflict'").fetchone()[0]
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
            if pruned:
                self._purge(con)
        log.info("Deadlines: %s from %s for %s: %d added, %d updated, %d unchanged, "
                 "%d skipped", len(clean), source, uid or "the owner", counts["added"],
                 counts["updated"], counts["unchanged"], counts["skipped"])
        return counts

    def _upsert(self, con, source: str, c: dict, now: float, counts: dict) -> set[str]:
        key = item_key(c["course"], c["title"])
        row = con.execute("SELECT * FROM items WHERE source = ? AND source_key = ?",
                          (source, c["source_key"])).fetchone()
        if row is None:
            if con.execute("SELECT 1 FROM tombstones WHERE source = ? AND key_hash = ?",
                           (source, _hash(c["source_key"]))).fetchone():
                counts["skipped"] += 1  # the person deleted it
                return set()
            total = con.execute("SELECT COUNT(*) FROM items").fetchone()[0]
            mine = con.execute("SELECT COUNT(*) FROM items WHERE source = ?",
                               (source,)).fetchone()[0]
            if total >= self.max_items or mine >= self.max_per_source:
                counts["skipped"] += 1
                return set()
            con.execute(
                "INSERT INTO items (source, source_key, item_key, course, title, kind, "
                "due, source_due, status, captured_at, sent_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (source, c["source_key"], key, c["course"], c["title"], c["kind"],
                 c["due"], c["due"], "draft", now, c.get("sent_at")))
            counts["added"] += 1
            return {key}
        if row["status"] == "dismissed":
            # stays dismissed; only what the source says now is noted
            con.execute("UPDATE items SET source_due = ? WHERE id = ?", (c["due"], row["id"]))
            counts["unchanged"] += 1
            return set()
        # a confirmed date is the person's answer: never overwritten
        due = row["due"] if row["status"] == "confirmed" else c["due"]
        sent = c.get("sent_at") if c.get("sent_at") is not None else row["sent_at"]
        new = (key, c["course"], c["title"], c["kind"], due, c["due"], sent)
        old = (row["item_key"], row["course"], row["title"], row["kind"], row["due"],
               row["source_due"], row["sent_at"])
        if new == old:
            counts["unchanged"] += 1
            return set()
        con.execute("UPDATE items SET item_key = ?, course = ?, title = ?, kind = ?, "
                    "due = ?, source_due = ?, sent_at = ? WHERE id = ?", (*new, row["id"]))
        counts["updated"] += 1
        return {key, row["item_key"]}

    def resolve(self, uid: str | None, item_id: int, choice: str,
                tz: tzinfo | None = None) -> dict | None:
        """The person's answer about one row: "confirm" (its date is right;
        other copies of the item that disagree are dismissed), "dismiss"
        (not a real deadline, or the wrong copy), or a date (the right
        date, confirmed). The updated row, or None if there is no such id."""
        choice = str(choice or "").strip()
        due = None
        if choice not in ("confirm", "dismiss"):
            due = normalize_due(choice, tz)  # DeadlineError: not a choice
        now = self.clock()
        with self._db(uid, write=True) as con:
            row = con.execute("SELECT * FROM items WHERE id = ?", (int(item_id),)).fetchone()
            if row is None:
                return None
            con.execute("BEGIN IMMEDIATE")
            try:
                if choice == "dismiss":
                    con.execute("UPDATE items SET status = 'dismissed' WHERE id = ?",
                                (row["id"],))
                else:
                    value = due or row["due"]
                    con.execute("UPDATE items SET status = 'confirmed', due = ?, "
                                "confirmed_value = ?, confirmed_at = ? WHERE id = ?",
                                (value, value, now, row["id"]))
                    # the other sources' copies that disagree were the wrong
                    # ones; never another item that shares the title
                    copies = con.execute(
                        "SELECT * FROM items WHERE item_key = ? AND status != "
                        "'dismissed'", (row["item_key"],)).fetchall()
                    singles = self._singles(copies)
                    for other in copies if row["id"] in singles else []:
                        if other["id"] in singles and other["source"] != row["source"] \
                                and not same_due(other["due"], value):
                            con.execute("UPDATE items SET status = 'dismissed' WHERE id = ?",
                                        (other["id"],))
                self._recompute(con, row["item_key"])
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
            fresh = con.execute("SELECT * FROM items WHERE id = ?", (row["id"],)).fetchone()
        log.info("Deadlines: %s %s item %d", uid or "the owner",
                 "dismissed" if choice == "dismiss" else "confirmed", row["id"])
        return self._public(fresh)

    def add(self, uid: str | None, title: str, due, course: str = "",
            kind: str = "assignment", tz: tzinfo | None = None) -> dict:
        """A deadline the person told us themselves: confirmed at once."""
        c = clean_candidate({"source_key": f"manual-{time.time_ns()}", "title": title,
                             "course": course, "kind": kind, "due": due}, tz)
        counts = {"added": 0, "updated": 0, "unchanged": 0, "skipped": 0}
        now = self.clock()
        with self._db(uid, write=True) as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                touched = self._upsert(con, "manual", c, now, counts)
                if not touched:
                    raise DeadlineError("too many deadlines saved; delete some first")
                rid = con.execute("SELECT id FROM items WHERE source = 'manual' AND "
                                  "source_key = ?", (c["source_key"],)).fetchone()["id"]
                con.execute("UPDATE items SET status = 'confirmed', confirmed_value = due, "
                            "confirmed_at = ? WHERE id = ?", (now, rid))
                for key in touched:
                    self._recompute(con, key)
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
            row = con.execute("SELECT * FROM items WHERE id = ?", (rid,)).fetchone()
        log.info("Deadlines: %s added item %d by hand", uid or "the owner", rid)
        return self._public(row)

    def delete(self, uid: str | None, item_id: int) -> bool:
        """Delete one row for good. A synced one leaves a tombstone (a hash
        of its source key) so the next sync doesn't bring it back."""
        with self._db(uid, write=False) as con:
            if con is None:
                return False
        with self._db(uid, write=True) as con:
            row = con.execute("SELECT id, source, source_key, item_key FROM items "
                              "WHERE id = ?", (int(item_id),)).fetchone()
            if row is None:
                return False
            con.execute("BEGIN IMMEDIATE")
            try:
                con.execute("DELETE FROM items WHERE id = ?", (row["id"],))
                if row["source"] != "manual":
                    con.execute("INSERT OR REPLACE INTO tombstones VALUES (?, ?, ?)",
                                (row["source"], _hash(row["source_key"]), self.clock()))
                    con.execute("DELETE FROM tombstones WHERE rowid NOT IN (SELECT rowid "
                                "FROM tombstones ORDER BY at DESC LIMIT ?)",
                                (MAX_TOMBSTONES,))
                self._recompute(con, row["item_key"])
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
            self._purge(con)
        log.info("Deadlines: %s deleted item %d", uid or "the owner", row["id"])
        return True

    def delete_source(self, uid: str | None, source: str) -> int:
        """Delete everything that came from `source` (its tombstones and
        read marks too: a clean slate). Returns the rows deleted."""
        if source not in SOURCES:
            raise DeadlineError("unknown source")
        with self._db(uid, write=False) as con:
            if con is None:
                return 0
        with self._db(uid, write=True) as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                keys = {r["item_key"] for r in con.execute(
                    "SELECT item_key FROM items WHERE source = ?", (source,))}
                gone = con.execute("DELETE FROM items WHERE source = ?", (source,)).rowcount
                con.execute("DELETE FROM tombstones WHERE source = ?", (source,))
                con.execute("DELETE FROM seen WHERE source = ?", (source,))
                for key in keys:
                    self._recompute(con, key)
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
            self._purge(con)
        log.info("Deadlines: %s deleted %d item(s) from %s", uid or "the owner", gone, source)
        return gone

    # ---------- what a sync already read (emails, pages) ----------
    def unseen(self, uid: str | None, source: str, keys: Iterable[str]) -> list[str]:
        """The keys (an email id, a page version) not read before."""
        keys = list(keys)
        with self._db(uid, write=False) as con:
            if con is None:
                return keys
            known = {r["key_hash"] for r in con.execute(
                "SELECT key_hash FROM seen WHERE source = ?", (source,))}
        return [k for k in keys if _hash(k) not in known]

    def mark_seen(self, uid: str | None, source: str, keys: Iterable[str]) -> None:
        now = self.clock()
        with self._db(uid, write=True) as con:
            con.executemany("INSERT OR REPLACE INTO seen VALUES (?, ?, ?)",
                            [(source, _hash(k), now) for k in keys])
            con.execute("DELETE FROM seen WHERE source = ? AND rowid NOT IN (SELECT rowid "
                        "FROM seen WHERE source = ? ORDER BY at DESC LIMIT ?)",
                        (source, source, MAX_SEEN))

    def note_sync(self, uid: str | None) -> None:
        with self._db(uid, write=True) as con:
            con.execute("INSERT OR REPLACE INTO meta VALUES ('last_sync', ?)",
                        (_iso(self.clock()),))


# One store per (owner dir, family dir) in this process, shared by the
# owner's tools (cli.register_student), Family and the routes
# (student_web), so they share one lock. A few at most (tests make more).
_STORES: OrderedDict[tuple[str, str], DeadlineStore] = OrderedDict()
_STORES_LOCK = threading.Lock()
MAX_STORES = 16


def open_store(config: dict) -> DeadlineStore:
    """The process's DeadlineStore for `config` (made on first use)."""
    s = config["student"]
    key = (str(Path(s["dir"]).resolve()), str(Path(config["family"]["dir"]).resolve()))
    with _STORES_LOCK:
        store = _STORES.get(key)
        if store is None:
            store = _STORES[key] = DeadlineStore.from_config(config)
            while len(_STORES) > MAX_STORES:
                _STORES.popitem(last=False)
        else:
            _STORES.move_to_end(key)
        store.config = config
        return store


def current() -> DeadlineStore | None:
    """The store opened last (the owner's routes on a server without
    family accounts), or None if none was."""
    with _STORES_LOCK:
        return next(reversed(_STORES.values()), None)
