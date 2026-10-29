"""The phone app's Memory screen (added to webapp.create_app).

Everyone's own memory (register_local; memory_store.py), for the owner and
each family member alike, each on their own store (webapp.WebAssistant.
memory_store_of): there is no person in any path, so nobody can name
someone else's memories, and an id from another person's store is a 404.

  GET  /api/memories                {"instructions", "paused", "memories":
                                     [{"id", "text", "created", "untrusted"}],
                                     "limits": {...}, "where": "cloud" | "pc" |
                                     "pi" (which machine keeps this file)}
  POST /api/memories/instructions   {"text"}: "Your instructions" (trusted:
                                     the person typed them)
  POST /api/memories/pause          {"paused": true|false}
  POST /api/memories/forget-all     {"confirm": true}: every memory and the
                                     instructions (the page asks first)
  GET  /api/memories/export         everything, as a JSON download
  POST /api/memories/{id}/update    {"text"} (keeps the untrusted flag)
  POST /api/memories/{id}/trust     "Keep as mine": no longer untrusted
  POST /api/memories/{id}/delete

Not signed in is 401; memory off is 404. Changes are POST (with failover
the page calls the Pi or PC cross-origin, and their preflight allows only
GET and POST), with webapp's Content-Length and cross-site checks. A
delete or "Forget all" also takes the text out of that person's open
conversations (WebAssistant.forget_memories); edits apply from the next
conversation. Logs carry ids and counts, never the text.

The owner's Pinecone notes ([memory] backend = "pinecone"; register):
what the assistant saved to long-term memory, by week, and deleting it.

  GET  /api/memory                 {"this_week", "last_week", "count",
                                    "truncated", "weeks": [{"week", "notes"}]}
  POST /api/memory/{id}/delete     one note (the page asks first)
  POST /api/memory/delete-week     {"week": "YYYY-MM-DD" | "unknown",
                                    "ids": [the ids the page showed]}

The owner's only: not signed in is 401, and a family member (who has no
memory) gets 404, as if the routes didn't exist; so does everyone when
memory is off. Deletes are POST (with failover the page calls the Pi or
PC cross-origin, and their preflight allows only GET and POST). "Delete
this week" deletes exactly the ids the page showed that are still in that
week, so a note saved in the meantime is never deleted unseen. Note text is
never logged here (memory_review.py logs ids and counts).
"""

# No `from __future__ import annotations` here: FastAPI must see the real
# Request class in the route signatures (it is imported inside register()).
import json
import logging

log = logging.getLogger(__name__)

MAX_IDS = 300  # per "delete this week" request (the cloud's body limit is 16 KB)
MAX_BODY = 64 * 1024  # 4000 characters of instructions, JSON-escaped, fit easily


def review_of(assistant):
    """The WebAssistant's MemoryReview (made from its memory), or None."""
    review = getattr(assistant, "memory_review", None)
    if review is None and getattr(assistant, "memory", None) is not None:
        from kwil.memory_review import MemoryReview

        review = assistant.memory_review = MemoryReview(assistant.memory)
    return review


def register(app, assistant, owner_of, error) -> None:
    """Add the routes. `owner_of(request)`: the owner's Session, None when
    not signed in; it raises a 404 for a family member's session."""
    from fastapi import Request
    from starlette.concurrency import run_in_threadpool

    from kwil.memory_review import UNKNOWN, own_id

    def guard(request):
        """(review, error response)."""
        if owner_of(request) is None:
            return None, error("not signed in", 401)
        review = review_of(assistant)
        if review is None:
            return None, error("not found", 404)
        return review, None

    def unreachable():
        return error("couldn't reach long-term memory; try again", 502)

    @app.get("/api/memory")
    async def list_memory(request: Request):
        review, err = guard(request)
        if err:
            return err
        try:
            return await run_in_threadpool(review.weeks)
        except Exception:
            log.exception("Listing memory failed")
            return unreachable()

    @app.post("/api/memory/delete-week")
    async def delete_week(request: Request):
        review, err = guard(request)
        if err:
            return err
        try:
            body = await request.json()
        except ValueError:
            return error("bad request", 400)
        week = body.get("week") if isinstance(body, dict) else None
        ids = body.get("ids") if isinstance(body, dict) else None
        if week is None:
            week = UNKNOWN
        if (not isinstance(week, str) or not isinstance(ids, list) or not ids
                or len(ids) > MAX_IDS or not all(isinstance(i, str) for i in ids)):
            return error("bad request", 400)
        try:
            deleted = await run_in_threadpool(review.delete_week, week, ids)
        except ValueError:
            return error("bad request", 400)
        except Exception:
            log.exception("Deleting a week of memory failed")
            return unreachable()
        return {"ok": True, "deleted": len(deleted), "ids": deleted}

    @app.post("/api/memory/{note_id}/delete")
    async def delete_note(note_id: str, request: Request):
        review, err = guard(request)
        if err:
            return err
        if not own_id(note_id):
            return error("not found", 404)
        try:
            found = await run_in_threadpool(review.delete, note_id)
        except Exception:
            log.exception("Deleting memory %s failed", note_id)
            return unreachable()
        if not found:
            return error("not found", 404)
        return {"ok": True}


def register_local(app, assistant, session_of, error) -> None:
    """Add everyone's own memory routes (see the module doc).
    `session_of(request)`: the signed-in Session, or None."""
    from fastapi import Request
    from fastapi.responses import JSONResponse
    from starlette.concurrency import run_in_threadpool

    from kwil.memory_store import (MAX_INSTRUCTIONS, MAX_MEMORIES,
                                   MAX_MEMORY_CHARS, valid_id)

    def guard(request):
        """(session, their store, error response)."""
        s = session_of(request)
        if s is None:
            return None, None, error("not signed in", 401)
        find = getattr(assistant, "memory_store_of", None)
        store = find(s) if callable(find) else None
        if store is None:
            return None, None, error("not found", 404)
        return s, store, None

    async def body_of(request) -> dict | None:
        data = await request.body()
        if len(data) > MAX_BODY:
            return None
        try:
            body = json.loads(data or b"{}")
        except ValueError:
            return None
        return body if isinstance(body, dict) else None

    def failed(what: str):
        log.exception("Memory: %s failed", what)
        return error("couldn't reach your memory; try again", 500)

    def drop(s, memory_ids) -> None:
        """Out of this person's open conversations too (rule: deleted is gone)."""
        forget = getattr(assistant, "forget_memories", None)
        if callable(forget):
            forget(s.user, memory_ids)

    def public(m: dict) -> dict:
        return {k: m[k] for k in ("id", "text", "created", "untrusted")}

    @app.get("/api/memories")
    async def memories_show(request: Request):
        s, store, err = guard(request)
        if err:
            return err
        try:
            memories = await run_in_threadpool(store.list)
            instructions = await run_in_threadpool(store.instructions)
            paused = await run_in_threadpool(lambda: store.paused)
        except Exception:
            return failed("listing")
        return {"instructions": instructions, "paused": paused,
                "memories": [public(m) for m in memories],
                "limits": {"instructions": MAX_INSTRUCTIONS,
                           "memory": MAX_MEMORY_CHARS, "count": MAX_MEMORIES},
                # each backend keeps its own file (not synced yet): say which
                "where": getattr(assistant, "memory_where", "") or ""}

    @app.post("/api/memories/instructions")
    async def memories_instructions(request: Request):
        s, store, err = guard(request)
        if err:
            return err
        body = await body_of(request)
        text = body.get("text") if body is not None else None
        if not isinstance(text, str):
            return error("bad request", 400)
        try:
            saved = await run_in_threadpool(store.set_instructions, text)
        except ValueError as e:
            return error(str(e), 400)
        except Exception:
            return failed("saving instructions")
        return {"ok": True, "instructions": saved}

    @app.post("/api/memories/pause")
    async def memories_pause(request: Request):
        s, store, err = guard(request)
        if err:
            return err
        body = await body_of(request)
        paused = body.get("paused") if body is not None else None
        if not isinstance(paused, bool):
            return error("bad request", 400)
        try:
            await run_in_threadpool(store.set_paused, paused)
        except Exception:
            return failed("pausing")
        return {"ok": True, "paused": paused}

    @app.post("/api/memories/forget-all")
    async def memories_forget_all(request: Request):
        s, store, err = guard(request)
        if err:
            return err
        body = await body_of(request)
        if body is None or body.get("confirm") is not True:
            return error("bad request", 400)
        try:
            count = await run_in_threadpool(store.forget_all)
        except Exception:
            return failed("forgetting everything")
        drop(s, None)
        return {"ok": True, "deleted": count}

    @app.get("/api/memories/export")
    async def memories_export(request: Request):
        s, store, err = guard(request)
        if err:
            return err
        try:
            data = await run_in_threadpool(store.export)
        except Exception:
            return failed("exporting")
        data["memories"] = [public(m) for m in data["memories"]]
        log.info("Memory: exported %d memories", len(data["memories"]))
        return JSONResponse(data, headers={
            "Content-Disposition": 'attachment; filename="kwil-memory.json"',
            "Cache-Control": "no-store"})

    @app.post("/api/memories/{memory_id}/update")
    async def memories_update(memory_id: str, request: Request):
        s, store, err = guard(request)
        if err:
            return err
        if not valid_id(memory_id):
            return error("not found", 404)
        body = await body_of(request)
        text = body.get("text") if body is not None else None
        if not isinstance(text, str):
            return error("bad request", 400)
        try:
            saved = await run_in_threadpool(store.update, memory_id, text)
        except ValueError as e:
            return error(str(e), 400)
        except Exception:
            return failed("editing")
        if saved is None:
            return error("not found", 404)
        return {"ok": True, "memory": public(saved)}

    @app.post("/api/memories/{memory_id}/trust")
    async def memories_trust(memory_id: str, request: Request):
        s, store, err = guard(request)
        if err:
            return err
        if not valid_id(memory_id):
            return error("not found", 404)
        try:
            saved = await run_in_threadpool(store.trust, memory_id)
        except Exception:
            return failed("keeping a memory")
        if saved is None:
            return error("not found", 404)
        return {"ok": True, "memory": public(saved)}

    @app.post("/api/memories/{memory_id}/delete")
    async def memories_delete(memory_id: str, request: Request):
        s, store, err = guard(request)
        if err:
            return err
        if not valid_id(memory_id):
            return error("not found", 404)
        try:
            found = await run_in_threadpool(store.delete, memory_id)
        except Exception:
            return failed("deleting")
        if not found:
            return error("not found", 404)
        drop(s, [memory_id])
        return {"ok": True}
