"""Memory review: what the assistant has saved about Kyle, so he can check
it and delete anything wrong.

Lists the assistant's own vectors in its own Pinecone namespace (ids
starting with "voice-" are conversation summaries and `remember` notes,
"note-" are quick notes, see memory.py and tools/notes.py) by id prefix
with `index.list`, reads their metadata with `index.fetch`, and groups them
by week (Monday first, in [assistant] timezone), newest first. Vectors
without a `created_at` show as "date unknown". Other namespaces (Kyle's
project notes) are never listed or deleted here.

Used by the phone app's Memory screen (memory_web.py), the read-only
`review_memory` voice tool, and the optional weekly nudge
([memory] weekly_review): once a week, the first conversation that has
notes from this week starts with "I saved N notes this week, want to
review them?".

Note text is only returned to the owner; it is never logged at INFO.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from kwil.memory import ID_PREFIX, list_ids
from kwil.tools import Tool, mark_untrusted
from kwil.tools.notes import NOTE_PREFIX
from kwil.tools.timeutil import local_zone, spoken_date

log = logging.getLogger(__name__)

# The ids this assistant writes (memory.store, notes.remember_note); only
# these are listed and only these can be deleted.
OWN_PREFIXES = (ID_PREFIX, NOTE_PREFIX)
MAX_NOTES = 1000        # listed at most (newest kept when there are more)
FETCH_BATCH = 100       # ids per fetch request
DELETE_BATCH = 1000     # Pinecone's limit per delete request
MAX_TEXT = 600          # characters of each note sent to the page
MAX_ID = 100
WEEK_SECONDS = 7 * 86400
CHECK_EVERY = 3600      # with no notes this week, look again after an hour
TOOL_NOTES = 20         # notes the voice tool returns at most
TOOL_TEXT = 300
UNKNOWN = "unknown"     # the week of notes with no date
_WEEK = re.compile(r"\d{4}-\d{2}-\d{2}")


def own_id(vector_id) -> bool:
    return (isinstance(vector_id, str) and 0 < len(vector_id) <= MAX_ID
            and vector_id.startswith(OWN_PREFIXES))


_STAMP = re.compile(r"\d{8}T\d{6}")


def _age_key(vector_id: str) -> tuple:
    """Newest first across prefixes: ids are "<prefix><UTC save time>-<hex>",
    so compare the part after the prefix; ids without a time sort oldest."""
    rest = vector_id[len(next(p for p in OWN_PREFIXES if vector_id.startswith(p))):]
    return (True, rest) if _STAMP.match(rest) else (False, rest)


def _kind(vector_id: str, meta: dict) -> str:
    kind = meta.get("kind")
    if kind == "note":
        return "note"
    if kind == "conversation_summary":
        return "summary"
    return "note" if vector_id.startswith("note-") else "summary"


class MemoryReview:
    """`memory` is a PineconeMemory (its index, namespace and delete)."""

    def __init__(self, memory, timezone: str | None = None,
                 weekly_review: bool = False, state_file: str | Path | None = None,
                 clock=time.time):
        self.memory = memory
        self.tz = local_zone(timezone)
        self.weekly_review = weekly_review
        self.state_file = Path(state_file) if state_file else None
        self.clock = clock
        self._lock = threading.Lock()
        self._last_check = None  # clock() of the last nudge check with no notes
        self._last_nudge = None  # also kept here: no state file, or it failed

    @property
    def index(self):
        return self.memory.index

    @property
    def namespace(self) -> str:
        return self.memory.namespace

    # ----- reading -----
    def _ids(self) -> tuple[list[str], bool]:
        """(the assistant's own ids, True if there were more than MAX_NOTES)."""
        ids = {vid for prefix in OWN_PREFIXES
               for vid in list_ids(self.index, prefix, self.namespace) if own_id(vid)}
        ids = sorted(ids, key=_age_key, reverse=True)
        return ids[:MAX_NOTES], len(ids) > MAX_NOTES

    def _fetch(self, ids: list[str]) -> dict:
        found = {}
        for i in range(0, len(ids), FETCH_BATCH):
            vectors = self.index.fetch(ids=ids[i:i + FETCH_BATCH],
                                       namespace=self.namespace).vectors
            for vid, v in (vectors or {}).items():
                meta = v.metadata if hasattr(v, "metadata") else v.get("metadata")
                found[vid] = meta or {}
        return found

    def _today(self) -> date:
        return datetime.fromtimestamp(self.clock(), self.tz).date()

    def week_start(self, day: date) -> date:
        return day - timedelta(days=day.weekday())

    def _entry(self, vector_id: str, meta: dict) -> dict:
        fields = getattr(self.memory, "text_fields", None)
        fields = fields if isinstance(fields, (list, tuple)) else ("text",)
        text = next((str(meta[f]) for f in fields if meta.get(f)), "")
        entry = {"id": vector_id, "kind": _kind(vector_id, meta),
                 "created_at": None, "date": None, "week": None,
                 "untrusted": meta.get("untrusted") is True,
                 "text": text.strip()[:MAX_TEXT]}
        created = meta.get("created_at")
        if isinstance(created, str):
            try:
                dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                local = dt.astimezone(self.tz).date()
                entry.update(created_at=dt.astimezone(timezone.utc)
                             .strftime("%Y-%m-%dT%H:%M:%SZ"),
                             date=local.isoformat(),
                             week=self.week_start(local).isoformat())
            except ValueError:
                pass
        return entry

    def notes(self) -> tuple[list[dict], bool]:
        """(every saved note and summary, newest first with undated ones
        last; True if the list was cut at MAX_NOTES)."""
        ids, truncated = self._ids()
        found = self._fetch(ids)
        entries = [self._entry(i, found[i]) for i in ids if i in found]
        entries.sort(key=lambda e: (e["created_at"] is not None,
                                    e["created_at"] or "", e["id"]), reverse=True)
        return entries, truncated

    def weeks(self) -> dict:
        """The Memory screen's list: notes grouped by week, newest first."""
        entries, truncated = self.notes()
        groups: dict = {}
        for e in entries:  # already newest first, so groups are too
            groups.setdefault(e["week"], []).append(e)
        this_week = self.week_start(self._today())
        log.info("Memory review: listed %d notes in %d weeks", len(entries), len(groups))
        return {"this_week": this_week.isoformat(),
                "last_week": (this_week - timedelta(days=7)).isoformat(),
                "count": len(entries), "truncated": truncated,
                "weeks": [{"week": w, "notes": notes} for w, notes in groups.items()]}

    def this_week(self, weeks_ago: int = 0) -> list[dict]:
        week = (self.week_start(self._today()) - timedelta(days=7 * weeks_ago)).isoformat()
        return [e for e in self.notes()[0] if e["week"] == week]

    # ----- deleting -----
    def delete(self, vector_id: str) -> bool:
        """Delete one of the assistant's own notes. False if there is no such
        note (or the id isn't one the assistant writes)."""
        if not own_id(vector_id) or vector_id not in self._fetch([vector_id]):
            return False
        self.memory.delete([vector_id])
        log.info("Memory review: deleted %s", vector_id)
        return True

    def delete_week(self, week: str, ids) -> list[str]:
        """Delete the notes the page showed for one week: exactly the `ids`
        that are still in that week now (a note saved since, or an id from
        another week, is left alone). Returns the ids deleted."""
        if not (week == UNKNOWN or _WEEK.fullmatch(str(week))):
            raise ValueError("bad week")
        wanted = list(dict.fromkeys(i for i in ids if own_id(i)))
        current = {e["id"] for e in self.notes()[0]
                   if (e["week"] or UNKNOWN) == week}
        chosen = [i for i in wanted if i in current]
        for i in range(0, len(chosen), DELETE_BATCH):
            self.memory.delete(chosen[i:i + DELETE_BATCH])
        log.info("Memory review: deleted %d notes from one week", len(chosen))
        return chosen

    # ----- weekly nudge -----
    def _load_state(self) -> dict:
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
            return state if isinstance(state, dict) else {}
        except (OSError, ValueError, AttributeError):
            return {}

    def _save_state(self, state: dict) -> None:
        if self.state_file is None:
            return
        from kwil.tools.google_auth import write_private

        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        write_private(self.state_file, json.dumps(state))

    def nudge(self) -> str | None:
        """For the start of a conversation: a system prompt line asking
        Claude to offer a review, at most once a week ([memory]
        weekly_review), and only when something was saved this week."""
        if not self.weekly_review:
            return None
        with self._lock:
            now = self.clock()
            last = self._load_state().get("last_nudge")
            last = max((t for t in (last, self._last_nudge)
                        if isinstance(t, (int, float))), default=None)
            if last is not None and now - last < WEEK_SECONDS:
                return None
            if self._last_check is not None and now - self._last_check < CHECK_EVERY:
                return None
            try:
                count = len(self.this_week())
            except Exception as e:  # memory is a bonus; never block a reply
                log.warning("Memory review: couldn't count this week's notes: %s", e)
                self._last_check = now
                return None
            if not count:
                self._last_check = now
                return None
            self._last_nudge = now
            try:
                self._save_state({"last_nudge": now})
            except OSError as e:
                log.warning("Memory review: couldn't save the nudge time: %s", e)
            log.info("Memory review: weekly nudge (%d notes)", count)
        noun = "note" if count == 1 else "notes"
        return (f"Weekly memory review: you saved {count} {noun} to Kyle's "
                f"long-term memory this week. Start this reply with one short "
                f"sentence like \"I saved {count} {noun} this week, want to review "
                f"them?\", then answer what he said. If he wants to, use "
                f"review_memory and go through them briefly; he can delete any "
                f"(delete_note asks him first) or use the Memory screen in the "
                f"phone app.")

    # ----- voice tool -----
    def review_memory(self, week: str = "this") -> dict:
        ago = 1 if str(week).lower() == "last" else 0
        entries = self.this_week(ago)
        start = self.week_start(self._today()) - timedelta(days=7 * ago)
        notes = [{"id": e["id"], "kind": e["kind"],
                  "day": spoken_date(date.fromisoformat(e["date"])),
                  "text": e["text"][:TOOL_TEXT]} for e in entries[:TOOL_NOTES]]
        return mark_untrusted({"week_of": spoken_date(start), "count": len(entries),
                               "notes": notes},
                              any(e["untrusted"] for e in entries[:TOOL_NOTES]))

    def tools(self) -> list[Tool]:
        return [Tool(
            "review_memory",
            "List what you saved to Kyle's long-term memory this week (or "
            "last week): conversation summaries and notes, newest first, with "
            "ids for delete_note. Use for 'what did you remember about me this "
            "week?'. Answer with a short spoken summary (a count and the main "
            "things), not every note.",
            {"type": "object", "properties": {
                "week": {"type": "string", "enum": ["this", "last"]}}},
            self.review_memory, private=True)]
