"""Conversation history API for the phone app (added to webapp.create_app).

  GET    /api/conversations              ?q= &source= &pinned= &archived=
                                         &since= &until= &limit= &offset=
                                         &project= (one of this person's
                                         projects, projects.py; else 404)
  GET    /api/conversations/{id}         the transcript
  POST   /api/conversations/{id}/branch  {"message_index"}: a new
                                         conversation with its messages up
                                         to that one ("Branch from here";
                                         the page binds its pane to it)
  POST   /api/conversations/{id}/continue  load it into this browser's chat
                                         (the voice view, older pages; the
                                         page's Continue is a GET, and its
                                         turns carry the id: POST /api/turn
                                         {"conversation_id"}, webapp.py)
  PATCH  /api/conversations/{id}         {"title"?, "pinned"?, "archived"?}
  DELETE /api/conversations/{id}         (the page asks first)
  GET    /api/conversations/{id}/export  ?format=md|json (a download)
  GET/POST /api/incognito                {"on": true|false}

Every route needs the signed-in session (401 otherwise) and goes through
the store, which only sees its owner's conversations; an unknown id and
someone else's id both give the same 404. Ids are random, so they can't be
enumerated. A family member (family.py) gets their own store and no memory
search, so Kyle's conversations are unknown ids to them.
"""

# No `from __future__ import annotations` here: FastAPI must see the real
# Request class in the route signatures (it is imported inside register()).
import logging
import re

from kwil import history, i18n

log = logging.getLogger(__name__)

MAX_PAGE = 50


def item(conv: dict) -> dict:
    """A conversation for the list: no summary text beyond the snippet."""
    return {"id": conv["id"], "title": conv["title"] or untitled(conv),
            "live": conv["ended_at"] is None,
            "source": conv["source"], "started_at": conv["started_at"],
            "updated_at": conv["updated_at"], "pinned": conv["pinned"],
            "archived": conv["archived"], "message_count": conv["message_count"],
            "match": conv.get("match"), "project": conv.get("project"),
            "snippet": [[t, m] for t, m in history.snippet_parts(conv.get("snippet", ""))]}


def untitled(conv: dict) -> str:
    """A conversation still going (titled when it ends): its first words."""
    preview = conv.get("preview") or ""
    return history.fallback_title([{"role": "user", "text": preview}])[0] \
        if preview else "New conversation"


def detail(conv: dict, active: bool = False) -> dict:
    return {**item(conv), "summary": conv["summary"], "ended_at": conv["ended_at"],
            "active": active, "redo": can_redo(conv["messages"]),
            "messages": [{"role": m["role"], "kind": m["kind"], "text": m["text"],
                          "created_at": m["created_at"],
                          "interrupted": m["interrupted"],
                          "tools": [{"tool": e.get("tool"), "status": e.get("status")}
                                    for e in m["tool_events"]]}
                         for m in conv["messages"]]}


def can_redo(messages: list[dict]) -> bool:
    """May the page offer "try again" and "edit" on this conversation's
    last turn (Kyle's last message and what followed): one that used no
    tool that may have changed something (webapp.start_redo checks again)."""
    from kwil.tools import changing_tools

    start = max((i for i, m in enumerate(messages)
                 if m["role"] == "user" and m["kind"] == "chat"), default=None)
    return start is not None and not changing_tools(
        e.get("tool") for m in messages[start:] for e in m["tool_events"])


def _bool(value):
    if value in (None, ""):
        return None
    return str(value).lower() in ("1", "true", "yes", "on")


def _day(value: str | None, end: bool = False):
    if not value:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("bad date")
    return history.parse_day(value, end=end)


def register(app, assistant, session_of, error) -> None:
    """Add the routes. `assistant` is the WebAssistant (with .history set to
    a HistoryStore, or None when history is off)."""
    from fastapi import Request
    from fastapi.responses import Response
    from starlette.concurrency import run_in_threadpool

    def guard(request):
        """(session, store, error response): the session's own store."""
        s = session_of(request)
        if s is None:
            return None, None, error("not signed in", 401)
        store, _ = assistant.history_of(s)
        if store is None:
            return s, None, error("history is off", 404)
        return s, store, None

    def memory_of(s):
        return assistant.history_of(s)[1]  # None for a family member

    @app.get("/api/conversations")
    def list_conversations(request: Request):
        s, store, err = guard(request)
        if err:
            return err
        p = request.query_params
        try:
            limit = max(1, min(int(p.get("limit", 20)), MAX_PAGE))
            offset = max(0, int(p.get("offset", 0)))
            filters = {"source": p.get("source") or None,
                       "pinned": _bool(p.get("pinned")),
                       "archived": False if p.get("archived") is None
                       else _bool(p.get("archived")),
                       "since": _day(p.get("since")),
                       "until": _day(p.get("until"), end=True)}
        except ValueError:
            return error("bad request", 400)
        if filters["source"] and filters["source"] not in history.SOURCES:
            return error("bad request", 400)
        project = p.get("project")
        if project:  # one of this person's own projects (else: unknown)
            find = getattr(assistant, "memory_store_of", None)
            mine = find(s) if callable(find) else None
            if mine is None or not callable(getattr(mine, "project", None)) \
                    or mine.project(project) is None:
                return error("not found", 404)
            filters["project"] = project
        query = (p.get("q") or "").strip()[:200]
        hits = history.search_conversations(
            store, query, memory_of(s), limit=limit + 1, offset=offset,
            **filters)
        more = len(hits) > limit
        return {"conversations": [item(c) for c in hits[:limit]],
                "next_offset": offset + limit if more else None,
                "query": query, "active": s.chat.conversation_id}

    @app.get("/api/conversations/{cid}")
    def get_conversation(cid: str, request: Request):
        s, store, err = guard(request)
        if err:
            return err
        try:
            return detail(store.get(cid), active=cid == s.chat.conversation_id)
        except KeyError:
            return error("not found", 404)

    @app.post("/api/conversations/{cid}/continue")
    async def continue_conversation(cid: str, request: Request):
        s, store, err = guard(request)
        if err:
            return err
        if s.chat.incognito:
            return error("turn incognito off to continue a conversation", 409)
        if not s.busy.acquire(timeout=2):
            return error("still working on the last request", 409)
        try:
            conv = await run_in_threadpool(s.chat.resume, cid)
        except KeyError:
            return error("not found", 404)
        finally:
            s.busy.release()
        s.last_active = assistant.clock()
        log.info("Web: continuing conversation %s", cid)
        return {"ok": True, **detail(store.get(cid), active=True),
                "title": conv["title"] or untitled(conv)}

    @app.post("/api/conversations/{cid}/branch")
    async def branch_conversation(cid: str, request: Request):
        """"Branch from here": a new conversation holding this one's
        messages up to and including {"message_index"} (as the transcript
        lists them, at most history.MAX_BRANCH_MESSAGES). Nothing changes
        in this browser's chat: the page binds its pane to the new id, and
        its first turn resumes it like any stored conversation (text only,
        and counted as outside text, as every resumed one is)."""
        s, store, err = guard(request)
        if err:
            return err
        if s.chat.incognito:
            return error("turn incognito off to branch a conversation", 409)
        try:
            body = await request.json()
        except ValueError:
            return error("bad request", 400)
        index = body.get("message_index") if isinstance(body, dict) else None
        if isinstance(index, bool) or not isinstance(index, int):
            return error("bad request", 400)
        try:
            new = await run_in_threadpool(store.branch, cid, index, 0,
                                          i18n.t("branch_title", s.language))
        except KeyError:
            return error("not found", 404)
        except IndexError:
            return error("no such message", 400)
        conv = store.get(new)
        log.info("Web: branched conversation %s at message %d into %s (%d messages)",
                 cid, index, new, conv["message_count"])
        return {"ok": True, **detail(conv)}

    @app.patch("/api/conversations/{cid}")
    async def update_conversation(cid: str, request: Request):
        s, store, err = guard(request)
        if err:
            return err
        try:
            body = await request.json()
        except ValueError:
            return error("bad request", 400)
        if not isinstance(body, dict) or not set(body) <= {"title", "pinned", "archived"}:
            return error("bad request", 400)
        title = body.get("title")
        if title is not None and (not isinstance(title, str) or len(title) > 200):
            return error("bad request", 400)
        flags = {k: body[k] for k in ("pinned", "archived") if k in body}
        if any(not isinstance(v, bool) for v in flags.values()):
            return error("bad request", 400)
        try:
            conv = store.update(cid, title=title, **flags)
        except KeyError:
            return error("not found", 404)
        except ValueError:
            return error("the title can't be empty", 400)
        return item(conv)

    @app.delete("/api/conversations/{cid}")
    async def delete_conversation(cid: str, request: Request):
        s, store, err = guard(request)
        if err:
            return err
        try:
            await run_in_threadpool(history.delete_conversation, store, cid,
                                    memory_of(s))
        except KeyError:
            return error("not found", 404)
        except Exception:
            log.exception("Deleting conversation %s failed", cid)
            return error("couldn't delete its memory summary; nothing was "
                         "deleted, try again", 502)
        assistant.forget_conversation(cid)
        return {"ok": True}

    @app.get("/api/conversations/{cid}/export")
    def export_conversation(cid: str, request: Request):
        s, store, err = guard(request)
        if err:
            return err
        fmt = request.query_params.get("format", "md")
        if fmt not in ("md", "json"):
            return error("bad request", 400)
        try:
            conv = store.get(cid)
        except KeyError:
            return error("not found", 404)
        name = re.sub(r"[^A-Za-z0-9]+", "-", conv["title"] or "conversation").strip("-")
        media = "application/json" if fmt == "json" else "text/markdown; charset=utf-8"
        return Response(history.export(conv, fmt), media_type=media, headers={
            "Content-Disposition":
                f'attachment; filename="{(name or "conversation")[:60]}.{fmt}"'})

    @app.get("/api/incognito")
    def get_incognito(request: Request):
        s = session_of(request)
        if s is None:
            return error("not signed in", 401)
        return {"on": s.chat.incognito}

    @app.post("/api/incognito")
    async def set_incognito(request: Request):
        s = session_of(request)
        if s is None:
            return error("not signed in", 401)
        try:
            body = await request.json()
        except ValueError:
            return error("bad request", 400)
        on = body.get("on") if isinstance(body, dict) else None
        if not isinstance(on, bool):
            return error("bad request", 400)
        if not await run_in_threadpool(assistant.set_incognito, s, on):
            return error("still working on the last request", 409)
        return {"on": s.chat.incognito}
