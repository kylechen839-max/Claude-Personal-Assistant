"""Quick capture: short notes Kyle asks to remember, kept in Pinecone.

"Remember that the lab report needs error bars" saves a note; "What did I
need for the lab report?" finds it by meaning. Notes live in the
assistant's own namespace (voice-assistant) with metadata kind=note,
created_at and tags, next to the conversation summaries, so ordinary
memory lookups before each reply can find them too. Only vectors whose id
starts with "note-" are ever deleted here.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone

from assistant.tools import UNTRUSTED_CONTEXT, Tool, mark_untrusted
from assistant.tools.timeutil import local_zone, spoken_date

log = logging.getLogger(__name__)

NOTE_PREFIX = "note-"
MAX_NOTE = 1000
MAX_TAGS = 8


def _clean_tags(tags) -> list[str]:
    if isinstance(tags, str):
        tags = re.split(r"[,;]", tags)
    out = []
    for tag in tags or []:
        tag = re.sub(r"\s+", " ", str(tag)).strip().lower()[:40]
        if tag and tag not in out:
            out.append(tag)
    return out[:MAX_TAGS]


class QuickNotes:
    """`memory` is a PineconeMemory: its index, namespace and embedder."""

    def __init__(self, memory, timezone: str | None = None,
                 min_score: float = 0.3, on_saved=None):
        self.memory = memory
        self.tz = local_zone(timezone)
        self.min_score = min_score
        # Called with each saved note's dict (e.g. to show it in history).
        self.on_saved = on_saved

    @property
    def index(self):
        return self.memory.index

    @property
    def namespace(self) -> str:
        return self.memory.namespace

    def _note(self, vector_id: str, meta: dict, score: float | None = None) -> dict:
        note = {"id": vector_id, "text": meta.get("text", "")}
        if meta.get("tags"):
            note["tags"] = list(meta["tags"])
        created = meta.get("created_at")
        if created:
            try:
                dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                note["saved"] = spoken_date(dt.astimezone(self.tz).date())
            except ValueError:
                pass
        if score is not None:
            note["score"] = round(float(score), 3)
        return note

    # ----- tools -----
    def remember_note(self, text: str, tags=None) -> dict:
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            raise ValueError("The note is empty")
        if len(text) > MAX_NOTE:
            raise ValueError(f"Notes are limited to {MAX_NOTE} characters")
        now = datetime.now(timezone.utc)
        vector_id = f"{NOTE_PREFIX}{now:%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
        metadata = {"text": text, "kind": "note", "source": "voice-assistant",
                    "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "tags": _clean_tags(tags)}
        if UNTRUSTED_CONTEXT.get():  # saved (after a yes) with web/email in context
            metadata["untrusted"] = True
        self.index.upsert(vectors=[{"id": vector_id,
                                    "values": self.memory.embed(text, "passage"),
                                    "metadata": metadata}],
                          namespace=self.namespace)
        log.info("Saved note %s", vector_id)
        note = self._note(vector_id, metadata)
        if self.on_saved:
            try:
                self.on_saved(note)
            except Exception:
                log.exception("Note hook failed")
        return {"status": "saved", "note": note}

    def recall_notes(self, query: str, limit: int = 5) -> dict:
        if not query.strip():
            raise ValueError("What should I look for?")
        result = self.index.query(vector=self.memory.embed(query, "query"),
                                  top_k=max(1, min(int(limit), 10)),
                                  namespace=self.namespace,
                                  filter={"kind": {"$eq": "note"}},
                                  include_metadata=True)
        hits = [m for m in result.matches if m.score >= self.min_score]
        notes = [self._note(m.id, m.metadata or {}, m.score) for m in hits]
        return mark_untrusted({"count": len(notes), "notes": notes},
                              any((m.metadata or {}).get("untrusted") for m in hits))

    def _ids(self) -> list[str]:
        ids = []
        for page in self.index.list(prefix=NOTE_PREFIX, namespace=self.namespace):
            ids.extend(page if isinstance(page, (list, tuple)) else [page])
        return ids

    def _fetch(self, ids: list[str]) -> dict:
        if not ids:
            return {}
        vectors = self.index.fetch(ids=ids, namespace=self.namespace).vectors
        return {vid: (v.metadata if hasattr(v, "metadata") else v.get("metadata"))
                or {} for vid, v in vectors.items()}

    def list_recent_notes(self, limit: int = 5) -> dict:
        # Ids start with the UTC save time, so they sort newest-last.
        ids = sorted(self._ids(), reverse=True)[:max(1, min(int(limit), 20))]
        found = self._fetch(ids)
        notes = [self._note(i, found[i]) for i in ids if i in found]
        return mark_untrusted({"count": len(notes), "notes": notes},
                              any(found[i].get("untrusted") for i in ids if i in found))

    def delete_note(self, note_id: str) -> dict:
        if not note_id.startswith(NOTE_PREFIX):
            raise ValueError("That isn't a saved note's id")
        self.index.delete(ids=[note_id], namespace=self.namespace)
        return {"status": "deleted", "id": note_id}

    def describe_for_delete(self, tool_input: dict) -> str:
        try:
            text = self._fetch([tool_input["note_id"]])[tool_input["note_id"]]["text"]
            short = text if len(text) <= 120 else text[:117] + "..."
            return f"Do you want me to delete the note: {short}?"
        except Exception:
            return "Do you want me to delete that note?"

    def describe_for_save(self, tool_input: dict) -> str:
        text = tool_input.get("text", "")
        short = text if len(text) <= 120 else text[:117] + "..."
        return f"Do you want me to save a note: {short}?"

    def tools(self) -> list[Tool]:
        return [
            Tool("remember_note",
                 "Save a short note Kyle asks you to remember ('remember "
                 "that...', 'make a note...'). Store it in his words, "
                 "self-contained (include what it is about), with a few "
                 "optional tags such as a course or project.",
                 {"type": "object", "properties": {
                     "text": {"type": "string"},
                     "tags": {"type": "array", "items": {"type": "string"},
                              "description": "e.g. ['physics', 'lab report']"}},
                  "required": ["text"]},
                 self.remember_note, confirm_if_untrusted=self.describe_for_save,
                 private=True),
            Tool("recall_notes",
                 "Search Kyle's saved notes by meaning, e.g. 'what did I "
                 "need for the lab report?'. Returns the best matches.",
                 {"type": "object", "properties": {
                     "query": {"type": "string"},
                     "limit": {"type": "integer", "minimum": 1, "maximum": 10}},
                  "required": ["query"]},
                 self.recall_notes, private=True),
            Tool("list_recent_notes",
                 "List Kyle's most recently saved notes, newest first, with "
                 "ids for delete_note.",
                 {"type": "object", "properties": {
                     "limit": {"type": "integer", "minimum": 1, "maximum": 20}}},
                 self.list_recent_notes, private=True),
            Tool("delete_note",
                 "Delete one saved note by id (from recall_notes or "
                 "list_recent_notes). Kyle is asked to confirm out loud "
                 "automatically; just call this tool.",
                 {"type": "object", "properties": {"note_id": {"type": "string"}},
                  "required": ["note_id"]},
                 self.delete_note, confirm_prompt=self.describe_for_delete,
                 private=True),
        ]
