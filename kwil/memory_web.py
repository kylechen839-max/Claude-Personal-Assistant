"""The phone app's Memory screen (added to webapp.create_app): what the
assistant saved to long-term memory, by week, and deleting it.

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
import logging

log = logging.getLogger(__name__)

MAX_IDS = 300  # per "delete this week" request (the cloud's body limit is 16 KB)


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
