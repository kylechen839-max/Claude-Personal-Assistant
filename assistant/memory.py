"""Long-term memory in Pinecone.

Before each reply the user's words are embedded and matched against the
existing index; good matches are added to the system prompt. After each
conversation Claude writes a short summary, which is stored as a new vector
in the assistant's own namespace (other namespaces are only read).

Embeddings use Pinecone's hosted `llama-text-embed-v2` at 1024 dimensions,
the model the existing `project-context` index was built with. Note text
is read from the first metadata field in `text_fields` that is present.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone

log = logging.getLogger(__name__)

SUMMARY_PROMPT = (
    "Below is a conversation between Kyle and his voice assistant. Write a "
    "short note (1-3 sentences) of anything worth remembering long-term: "
    "facts about Kyle, his preferences, plans, decisions, or things he asked "
    "to remember. Include dates when relevant. If nothing is worth "
    "remembering (small talk, one-off questions), reply with exactly NOTHING."
)


class PineconeMemory:
    def __init__(self, index_name: str = "project-context",
                 namespace: str = "voice-assistant",
                 search_namespaces: list[str] | str = "*",
                 embed_model: str = "llama-text-embed-v2", dimension: int = 1024,
                 top_k: int = 4, min_score: float = 0.4,
                 max_note_chars: int = 600, api_key: str | None = None,
                 text_fields=("text", "chunk_text", "content", "summary"),
                 pc=None, index=None):
        self.index_name = index_name
        self.namespace = namespace
        self.search_namespaces = search_namespaces
        self.embed_model = embed_model
        self.dimension = dimension
        self.top_k = top_k
        self.min_score = min_score
        self.max_note_chars = max_note_chars
        self.text_fields = ([text_fields] if isinstance(text_fields, str)
                            else list(text_fields))
        self.api_key = api_key
        self._pc = pc
        self._index = index
        self._namespaces: list[str] | None = None
        self.last_untrusted = False  # set by retrieve()

    @property
    def pc(self):
        if self._pc is None:
            from pinecone import Pinecone

            self._pc = Pinecone(api_key=self.api_key or os.environ["PINECONE_API_KEY"])
        return self._pc

    @property
    def index(self):
        if self._index is None:
            self._index = self.pc.Index(self.index_name)
        return self._index

    def embed(self, text: str, input_type: str) -> list[float]:
        params = {"input_type": input_type, "truncate": "END"}
        if self.embed_model == "llama-text-embed-v2":
            params["dimension"] = self.dimension
        result = self.pc.inference.embed(model=self.embed_model, inputs=[text],
                                         parameters=params)
        return list(result.data[0]["values"])

    def namespaces(self) -> list[str]:
        if isinstance(self.search_namespaces, str) and self.search_namespaces != "*":
            return [self.search_namespaces]  # a single namespace name
        if self.search_namespaces != "*":
            return list(self.search_namespaces)
        if self._namespaces is None:
            stats = self.index.describe_index_stats()
            self._namespaces = sorted(set((stats.namespaces or {}).keys())
                                      | {self.namespace})
        return self._namespaces

    def retrieve(self, query: str) -> list[str]:
        """Return the text of notes relevant to `query`, best first."""
        if not query.strip():
            return []
        vector = self.embed(query, "query")
        namespaces = self.namespaces()
        if len(namespaces) == 1:
            result = self.index.query(vector=vector, top_k=self.top_k,
                                      namespace=namespaces[0],
                                      include_metadata=True)
        else:
            result = self.index.query_namespaces(
                vector=vector, namespaces=namespaces, metric="cosine",
                top_k=self.top_k, include_metadata=True)
        notes = []
        self.last_untrusted = False
        for match in result.matches:
            meta = match.metadata or {}
            text = next((str(meta[f]) for f in self.text_fields if meta.get(f)),
                        "").strip()
            if match.score >= self.min_score and text:
                notes.append(text[: self.max_note_chars])
                # Written from untrusted text (e.g. an email): the turn that
                # uses it must confirm changes (see ClaudeChat.recall).
                self.last_untrusted |= meta.get("untrusted") is True
        log.info("Memory: %d notes", len(notes))
        return notes

    def store(self, summary: str, extra: dict | None = None) -> str:
        now = datetime.now(timezone.utc)
        vector_id = f"voice-{now:%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
        metadata = {"text": summary, "source": "voice-assistant",
                    "kind": "conversation_summary",
                    "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), **(extra or {})}
        self.index.upsert(vectors=[{"id": vector_id,
                                    "values": self.embed(summary, "passage"),
                                    "metadata": metadata}],
                          namespace=self.namespace)
        log.info("Memory: stored %s", vector_id)
        return vector_id


    def search_conversations(self, query: str, top_k: int = 5,
                             min_score: float | None = None) -> list[dict]:
        """Semantic search over the assistant's own conversation summaries
        that link back to a saved conversation. Best first:
        [{"conversation_id", "score", "text"}]."""
        if not query.strip():
            return []
        result = self.index.query(
            vector=self.embed(query, "query"), top_k=top_k,
            namespace=self.namespace, include_metadata=True,
            filter={"conversation_id": {"$exists": True}})
        floor = self.min_score if min_score is None else min_score
        hits = []
        for match in result.matches:
            meta = match.metadata or {}
            if meta.get("conversation_id") and match.score >= floor:
                hits.append({"conversation_id": str(meta["conversation_id"]),
                             "score": float(match.score),
                             "text": str(meta.get("text", ""))[: self.max_note_chars]})
        return hits

    def vectors_for(self, conversation_id: str, top_k: int = 100) -> list[str]:
        """Ids of this namespace's summaries of one saved conversation (by
        their conversation_id metadata), wherever they were written."""
        result = self.index.query(
            vector=self.embed("conversation summary", "query"), top_k=top_k,
            namespace=self.namespace, include_metadata=False,
            filter={"conversation_id": {"$eq": conversation_id}})
        return [m.id for m in result.matches]

    def delete(self, vector_ids: list[str]) -> None:
        """Delete summaries (by vector id) from the assistant's namespace."""
        if vector_ids:
            self.index.delete(ids=list(vector_ids), namespace=self.namespace)
            log.info("Memory: deleted %d vectors", len(vector_ids))


def transcript(history: list[dict], user_only: bool = False) -> str:
    """Plain-text transcript of user text and assistant text (no tool data).
    `user_only`: just Kyle's lines (when untrusted text was in context)."""
    lines = []
    for msg in history:
        if user_only and msg["role"] != "user":
            continue
        content = msg["content"]
        if isinstance(content, str):
            text = content
        else:
            text = " ".join(b.get("text", "") for b in content
                            if b.get("type") == "text")
        text = text.strip()
        if text and text != "(no reply)":
            lines.append(f"{'Kyle' if msg['role'] == 'user' else 'Assistant'}: {text}")
    return "\n".join(lines)
