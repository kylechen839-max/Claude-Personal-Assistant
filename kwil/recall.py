"""Memory layer 2: find and read past conversations ("what did we decide
about my physics schedule last week?") without Pinecone.

A `Retriever` finds the past conversations that match a question. Every
person's retriever reads only their own HistoryStore (the owner's
`data/history.sqlite`, a family member's `<dir>/<id>/history.sqlite`), so
one person's search can never return another's conversation.

- `FtsRetriever` (always): SQLite FTS5 over titles, summaries and message
  text (HistoryStore.search: bm25, a title or summary match ranks above a
  word said in passing). The index is kept by triggers, so a conversation
  is searchable from its first message and gone with its delete.
- `HybridRetriever` (`[memory] embeddings = "fastembed"`): the same, plus
  one vector per conversation made from its title and summary, stored in
  the same SQLite file (`recall_vectors`, deleted with the conversation by
  ON DELETE CASCADE). Search is brute-force cosine over at most
  `history.MAX_RECALL_SCAN` vectors, fused with the keyword hits by
  reciprocal rank fusion, so a paraphrase ("physics revision timetable"
  for "mechanics exam study plan") is found too. Vectors are made in the
  background (`backfill`): at startup, after a conversation's summary is
  written, and on a search that finds some missing. A search never waits.

Incognito conversations are never written to the store, so never indexed.
Archived ones are searchable; deleted ones never.

Embedders are pluggable (`Embedder`): `FastEmbedder` uses fastembed (an
optional package, imported only when embeddings are on; its model is
downloaded once at setup with `python -m kwil.recall --download`, never
during a conversation). Tests use a fake embedder.

Scaling up (a public, many-user server): add a class with the same three
methods backed by Postgres + pgvector, e.g. a table
`recall(owner, conversation_id, title, summary, updated_at, archived,
tsv tsvector, embedding vector(384))` with a GIN index on `tsv` and an HNSW
index on `embedding`, every query `WHERE owner = $1`; `index()` upserts a
row from `HistoryStore.get`, `delete()` removes it, and `search()` runs
the same reciprocal rank fusion over `ts_rank` and `<=>` results. Attach it
with `store.recall = PgRetriever(...)`; `history.delete_conversation`
already calls `delete()`. Deletes that bypass it (sync tombstones, prune)
need a periodic sweep of ids no longer in the store.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path
from typing import Protocol, Sequence

from kwil import history

log = logging.getLogger(__name__)

RRF_K = 60            # reciprocal rank fusion constant (the usual 60)
CANDIDATES = 50       # hits taken from each list before fusing
BACKFILL_BATCH = 16   # conversations embedded per call
BACKFILL_MAX = 500    # per run; the next run (search, finish) goes on
SNIPPET_TOKENS = 12   # words around a keyword match (the phone's History)
TOOL_SNIPPET_TOKENS = 24  # what search_conversations asks for (tools/history.py)
FUSE_MAX = 200        # past this many results, paging is keyword-only
RETRY_AFTER = 600     # seconds before a failed embedder is tried again
SUMMARY_SNIPPET = 160  # characters of the summary for a vector-only hit
EMBED_CHARS = 1000    # text per conversation given to the embedder


class Retriever(Protocol):
    """Finds past conversations in one person's history."""

    def index(self, conversation: dict) -> bool:
        """Make `conversation` (HistoryStore.get's shape) findable. False if
        nothing was stored (e.g. it was deleted meanwhile)."""

    def search(self, query: str, k: int = 10, filters: dict | None = None,
               offset: int = 0, snippet_tokens: int = SNIPPET_TOKENS) -> list[dict]:
        """Best first: conversation dicts (HistoryStore.list's shape) with
        `snippet`, `score` and `match` ("keyword" or "semantic").
        `filters`: source, pinned, archived, since, until, project.
        `snippet_tokens`: words around a keyword match."""

    def delete(self, conversation_id: str) -> None:
        """Forget a conversation's index entries."""

    def index_soon(self, conversation_id: str) -> None:
        """A conversation's title or summary changed: index it, off the
        caller's thread."""


class Embedder(Protocol):
    name: str  # stored with each vector: a new model re-embeds everything

    def embed(self, texts: list[str]) -> list[Sequence[float]]:
        """One vector per text (passages: titles and summaries)."""

    def embed_query(self, text: str) -> Sequence[float]:
        """The vector for a search query."""


# ---------- keyword search ----------
class FtsRetriever:
    """FTS5 over titles, summaries and messages (HistoryStore.search)."""

    def __init__(self, store: history.HistoryStore):
        self.store = store

    def index(self, conversation: dict) -> bool:
        return True  # the store's FTS triggers index every message and title

    def index_soon(self, conversation_id: str) -> None:
        pass

    def delete(self, conversation_id: str) -> None:
        pass  # the FTS rows go with the conversation's own rows

    def search(self, query: str, k: int = 10, filters: dict | None = None,
               offset: int = 0, snippet_tokens: int = SNIPPET_TOKENS) -> list[dict]:
        hits = self.store.search(query, limit=k, offset=offset,
                                 snippet_tokens=snippet_tokens,
                                 **_store_filters(filters))
        for h in hits:
            h["match"] = "keyword"
        return hits


def _store_filters(filters: dict | None) -> dict:
    f = dict(filters or {})
    return {"source": f.get("source"), "pinned": f.get("pinned"),
            "archived": f.get("archived", False), "since": f.get("since"),
            "until": f.get("until"), "project": f.get("project")}


# ---------- keyword + vectors ----------
def _unit(vector) -> bytes | None:
    """float32 bytes of `vector` scaled to length 1 (None if all zero)."""
    import numpy as np

    v = np.asarray(vector, dtype=np.float32).ravel()
    norm = float(np.linalg.norm(v))
    if not v.size or norm == 0 or not np.isfinite(norm):
        return None
    return (v / norm).astype(np.float32).tobytes()


def conversation_text(conv: dict) -> str:
    """What a conversation's vector is made from: its title and summary
    (the first thing said, when there is no summary yet)."""
    parts = [conv.get("title") or "", conv.get("summary") or conv.get("preview") or ""]
    return ". ".join(p.strip() for p in parts if p and p.strip())[:EMBED_CHARS]


class HybridRetriever:
    """FtsRetriever plus summary vectors, fused by reciprocal rank."""

    def __init__(self, store: history.HistoryStore, embedder: Embedder, *,
                 min_score: float = 0.5, run_async: bool = True,
                 clock=time.monotonic):
        self.store = store
        self.clock = clock
        self.embedder = embedder
        self.min_score = float(min_score)
        self.run_async = run_async
        self.fts = FtsRetriever(store)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._again = False
        # The embedder failed at this time (clock): keyword search only,
        # until RETRY_AFTER has passed or its model folder changed (e.g.
        # `python -m kwil.recall --download` ran meanwhile).
        self._failed_at: float | None = None
        self._failed_dir = None

    def _model_dir_state(self):
        """The model folder's modification time (None if there is none)."""
        folder = getattr(self.embedder, "cache_dir", None)
        try:
            return Path(folder).stat().st_mtime_ns if folder else None
        except OSError:
            return None

    def _fail(self, e: Exception) -> None:
        self._failed_at = self.clock()
        self._failed_dir = self._model_dir_state()
        log.warning("Recall: embeddings unavailable (%s); keyword search only "
                    "for now", type(e).__name__)

    @property
    def _broken(self) -> bool:
        """The embedder failed lately; cleared after a cool-down, or as
        soon as its model folder changed."""
        if self._failed_at is None:
            return False
        if self.clock() - self._failed_at >= RETRY_AFTER or \
                self._model_dir_state() != self._failed_dir:
            self._failed_at = None
            log.info("Recall: trying embeddings again")
            return False
        return True

    @property
    def model(self) -> str:
        return str(getattr(self.embedder, "name", "embedder"))

    # --- indexing ---
    def index(self, conversation: dict) -> bool:
        """Embed one conversation now (on the caller's thread)."""
        pending = self.store.recall_pending(self.model, 1, ids=[conversation["id"]])
        if not pending:  # already current, deleted, or not this person's
            return bool(self.store.list(ids=[conversation["id"]], archived=None))
        return self._index_batch(pending) == 1

    def _index_batch(self, convs: list[dict]) -> int:
        texts = [conversation_text(c) or "(empty)" for c in convs]
        vectors = self.embedder.embed(texts)
        done = 0
        for conv, vector in zip(convs, vectors):
            blob = _unit(vector)
            if blob is not None and self.store.put_recall_vector(
                    conv["id"], self.model, conv["version"], blob, len(blob) // 4):
                done += 1
        return done

    def backfill(self, limit: int = BACKFILL_MAX) -> int:
        """Embed up to `limit` conversations that have no current vector,
        newest first, a batch at a time. Resumable: each batch is saved as
        it is made, so a stop (crash, restart) loses at most one batch and
        the next run goes on from there. Returns how many were indexed."""
        if self._broken:
            return 0
        done = 0
        try:
            while done < limit:
                batch = self.store.recall_pending(self.model,
                                                  min(BACKFILL_BATCH, limit - done))
                if not batch:
                    break
                n = self._index_batch(batch)
                done += n
                if n == 0:
                    break  # all deleted meanwhile, or unusable vectors
        except Exception as e:  # keyword search still works
            self._fail(e)
        if done:
            log.info("Recall: indexed %d conversation(s), %d left", done,
                     self.store.recall_pending_count(self.model))
        return done

    def start_backfill(self) -> None:
        """backfill() on a background thread (one at a time; a request
        while it runs makes it go round once more)."""
        if self._broken:
            return
        if not self.run_async:
            self.backfill()
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self._again = True
                return
            self._thread = threading.Thread(target=self._run, name="recall-backfill",
                                            daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while True:
            try:
                self.backfill()
            except Exception:  # never let the thread die loudly mid-turn
                log.exception("Recall: backfill failed")
            with self._lock:
                if not self._again:
                    self._thread = None
                    return
                self._again = False

    def wait(self, timeout: float = 30) -> None:
        """For tests and shutdown: wait for a running backfill."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def index_soon(self, conversation_id: str) -> None:
        self.start_backfill()

    def delete(self, conversation_id: str) -> None:
        self.store.delete_recall_vectors(conversation_id)

    # --- searching ---
    def search(self, query: str, k: int = 10, filters: dict | None = None,
               offset: int = 0, snippet_tokens: int = SNIPPET_TOKENS) -> list[dict]:
        k, offset = max(1, int(k)), max(0, int(offset))
        if not history.query_terms(query):
            # no words: newest first
            return self.fts.search(query, k=k, filters=filters, offset=offset,
                                   snippet_tokens=snippet_tokens)
        if offset + k > FUSE_MAX:
            # deep pages: the fused top FUSE_MAX, then the keyword hits it
            # doesn't hold, in keyword order, so paging never skips or
            # repeats a conversation
            head = self.search(query, k=FUSE_MAX, filters=filters,
                               snippet_tokens=snippet_tokens)
            shown = {h["id"] for h in head}
            rest, at = [], 0
            while len(head) + len(rest) < offset + k:
                batch = self.fts.search(query, k=FUSE_MAX, filters=filters,
                                        offset=at, snippet_tokens=snippet_tokens)
                rest += [h for h in batch if h["id"] not in shown]
                if len(batch) < FUSE_MAX:
                    break
                at += FUSE_MAX
            return (head + rest)[offset:offset + k]
        # one fixed-size fused list for every page, so pages never overlap
        want = FUSE_MAX
        keyword = self.fts.search(query, k=max(want, CANDIDATES), filters=filters,
                                  snippet_tokens=snippet_tokens)
        semantic = self._vector_search(query, max(want, CANDIDATES), filters)
        if self.store.recall_pending_count(self.model):
            self.start_backfill()  # never waited for: this search uses what's there
        if not semantic:
            return keyword[offset:offset + k]
        fused: dict[str, float] = {}
        for ranked in ([h["id"] for h in keyword], [cid for cid, _ in semantic]):
            for rank, cid in enumerate(ranked):
                fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
        by_id = {h["id"]: h for h in keyword}
        similarity = dict(semantic)
        missing = [cid for cid in similarity if cid not in by_id]
        if missing:
            f = _store_filters(filters)
            for conv in self.store.list(limit=200, ids=missing, **f):
                conv.update(match="semantic",
                            snippet=history._truncate(conv["summary"] or conv["preview"],
                                                      SUMMARY_SNIPPET))
                by_id[conv["id"]] = conv
        order = sorted((cid for cid in fused if cid in by_id),
                       key=lambda cid: -fused[cid])
        hits = []
        for cid in order[offset:offset + k]:
            hit = by_id[cid]
            hit["score"] = round(fused[cid], 6)
            if cid in similarity:
                hit["similarity"] = round(similarity[cid], 4)
            hits.append(hit)
        return hits

    def _vector_search(self, query: str, k: int,
                       filters: dict | None) -> list[tuple[str, float]]:
        """[(conversation id, cosine)] at least min_score, best first."""
        if self._broken:
            return []
        import numpy as np

        rows = self.store.recall_vectors(self.model, **_store_filters(filters))
        if not rows:
            return []
        try:
            q = _unit(self.embedder.embed_query(query))
        except Exception as e:
            self._fail(e)
            return []
        if q is None:
            return []
        qv = np.frombuffer(q, dtype=np.float32)
        rows = [r for r in rows if r[2] == qv.size]
        if not rows:
            return []
        matrix = np.frombuffer(b"".join(r[1] for r in rows),
                               dtype=np.float32).reshape(len(rows), qv.size)
        scores = matrix @ qv
        best = np.argsort(-scores)[:k]
        return [(rows[i][0], float(scores[i])) for i in best
                if scores[i] >= self.min_score]


# ---------- embedders ----------
class FastEmbedder:
    """fastembed's ONNX text models (CPU; bge-small is ~130 MB, 384 dims).
    Optional: fastembed is imported on first use, and the model is read
    from `cache_dir` only (`download()` fetches it once, at setup)."""

    def __init__(self, model: str = "BAAI/bge-small-en-v1.5",
                 cache_dir: str = "models/fastembed"):
        self.name = f"fastembed:{model}"
        self.model = model
        self.cache_dir = cache_dir
        self._impl = None
        self._lock = threading.Lock()

    def _load(self, local_only: bool = True):
        with self._lock:
            if self._impl is None:
                from fastembed import TextEmbedding  # optional dependency

                self._impl = TextEmbedding(model_name=self.model,
                                           cache_dir=self.cache_dir,
                                           local_files_only=local_only)
            return self._impl

    def download(self) -> None:
        self._load(local_only=False)

    def embed(self, texts: list[str]) -> list[Sequence[float]]:
        return list(self._load().embed(list(texts)))

    def embed_query(self, text: str) -> Sequence[float]:
        impl = self._load()
        fn = getattr(impl, "query_embed", None) or impl.embed
        return next(iter(fn([text])))


_EMBEDDERS: dict[tuple, Embedder] = {}
_EMBEDDERS_LOCK = threading.Lock()


def embedder_from_config(config: dict) -> Embedder | None:
    """The configured embedder ([memory] embeddings), one per process
    (shared by every person's store), or None when off."""
    mem = config.get("memory", {})
    kind = str(mem.get("embeddings") or "off").lower()
    if kind in ("off", "false", "none", ""):
        return None
    if kind != "fastembed":
        log.warning("Recall: unknown [memory] embeddings %r; keyword search only", kind)
        return None
    key = (kind, mem.get("embedding_model"), mem.get("embedding_dir"))
    with _EMBEDDERS_LOCK:
        if key not in _EMBEDDERS:
            _EMBEDDERS[key] = FastEmbedder(mem.get("embedding_model")
                                           or "BAAI/bge-small-en-v1.5",
                                           mem.get("embedding_dir")
                                           or "models/fastembed")
        return _EMBEDDERS[key]


def attach(store: history.HistoryStore | None, config: dict,
           embedder: Embedder | None = None) -> Retriever | None:
    """Give `store` its Retriever (store.recall) and, with embeddings on,
    start indexing what isn't yet (in the background). Returns it."""
    if store is None:
        return None
    embedder = embedder or embedder_from_config(config)
    if embedder is None:
        store.recall = FtsRetriever(store)
    else:
        store.recall = HybridRetriever(
            store, embedder,
            min_score=config.get("memory", {}).get("embedding_min_score", 0.5))
        store.recall.start_backfill()
    return store.recall


def retriever_for(store: history.HistoryStore) -> Retriever:
    """The store's Retriever, or keyword search when none was attached."""
    return store.recall if store.recall is not None else FtsRetriever(store)


# ---------- reading a past conversation (tools/history.py) ----------
def _patterns(query: str) -> list[re.Pattern]:
    return [re.compile(r"\b" + re.escape(t), re.IGNORECASE)
            for t in history.query_terms(query)]


def excerpt(messages: list[dict], query: str = "", limit: int = 20,
            radius: int = 3) -> tuple[list[int], set[int]]:
    """Which messages to show: (indexes in order, indexes that matched).
    With `query`: the messages around the best matches (each window
    centered on its match: the match, then one before, one after, ...),
    at most `limit`. With no query, or nothing matching: the last `limit`."""
    limit = max(1, int(limit))
    patterns = _patterns(query) if query else []
    scores = {i: sum(1 for p in patterns if p.search(m.get("text", "")))
              for i, m in enumerate(messages)} if patterns else {}
    matches = sorted((i for i, s in scores.items() if s), key=lambda i: (-scores[i], i))
    if not matches:
        return list(range(max(0, len(messages) - limit), len(messages))), set()
    chosen: set[int] = set()
    for i in matches:
        for d in range(radius + 1):
            for j in ((i,) if d == 0 else (i - d, i + d)):
                if 0 <= j < len(messages) and len(chosen) < limit:
                    chosen.add(j)
        if len(chosen) >= limit:
            break
    return sorted(chosen), {i for i in matches if i in chosen}


def clip(text: str, query: str = "", n: int = 500) -> str:
    """`text` cut to `n` characters, around the first match of `query`."""
    text = " ".join((text or "").split())
    if len(text) <= n:
        return text
    pos = min((m.start() for p in _patterns(query) for m in [p.search(text)] if m),
              default=0)
    start = max(0, min(pos - n // 4, len(text) - (n - 2)))
    piece = text[start:start + n - 2].strip()
    return (history.ELLIPSIS if start else "") + piece + \
        (history.ELLIPSIS if start + n - 2 < len(text) else "")


# ---------- setup: python -m kwil.recall --download ----------
def main(argv=None) -> int:
    import argparse

    from kwil.config import load_config

    parser = argparse.ArgumentParser(
        prog="python -m kwil.recall",
        description="Conversation recall: download the embedding model "
                    "([memory] embeddings) or index saved conversations.")
    parser.add_argument("--download", action="store_true",
                        help="download the embedding model once (setup)")
    parser.add_argument("--backfill", action="store_true",
                        help="index every saved conversation now")
    args = parser.parse_args(argv)
    config = load_config()
    embedder = embedder_from_config(config)
    if embedder is None:
        print("[memory] embeddings is off: keyword search needs no setup.")
        return 0
    if args.download:
        try:
            embedder.download()
        except ImportError:
            print("fastembed isn't installed: pip install fastembed")
            return 1
        print(f"Embedding model ready in {config['memory']['embedding_dir']}.")
    if args.backfill:
        store = history.open_store(config)
        if store is None:
            print("Conversation history is off.")
            return 1
        retriever = HybridRetriever(store, embedder)
        total = 0
        while True:
            n = retriever.backfill()
            total += n
            if n == 0:
                break
        print(f"Indexed {total} conversation(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
