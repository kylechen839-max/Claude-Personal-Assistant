"""The phone app's Projects (projects.py), added to webapp.create_app.

Everyone's own, for the owner and each family member alike, on their own
memory store (webapp.WebAssistant.memory_store_of): there is no person in
any path, so nobody can name someone else's project, and an id from
another person's store is a 404, as is everything when projects are off
for them (no search_project_files in their tools: [projects] enabled =
false, or memory off).

  GET  /api/projects                  {"projects": [{"id", "name",
                                       "instructions", "course", "created",
                                       "updated", "memories", "files"}],
                                       "limits": {...}}
  POST /api/projects                  {"name", "instructions"?, "course"?}
                                       (course: a suggestion's key; the
                                       same one twice makes one project)
  GET  /api/projects/suggestions      {"suggestions": [{"name", "course",
                                       "source", "project"}]} from their own
                                       Canvas courses and deadlines (the
                                       owner's Notion Course options too)
  GET  /api/projects/{id}             {"project", "files", "memories",
                                       "conversations"} (the project page)
  POST /api/projects/{id}/update      {"name"?, "instructions"?}
  POST /api/projects/{id}/delete      {"confirm": true}: the project, its
                                       memories and files (gone); its
                                       conversations stay, in no project
  POST /api/projects/{id}/files       a multipart form with one "file"
                                       (a PDF or text file; projects.
                                       prepare_file checks it)
  POST /api/projects/{id}/files/{file id}/delete

A project's memories are edited and deleted with the Memory routes
(memory_web.py: one id space). A new chat in a project is no route of its
own: the page's turns carry {"project_id"} (webapp's /api/turn).

Not signed in is 401. Changes are POST (the failover preflight allows only
GET and POST), with webapp's Content-Length and cross-site checks. Logs
carry ids, counts, kinds and sizes, never a name, instructions, a memory,
a file name or its text.
"""

# No `from __future__ import annotations` here: FastAPI must see the real
# Request class in the route signatures (it is imported inside register()).
import json
import logging

log = logging.getLogger(__name__)

MAX_BODY = 64 * 1024  # 4000 characters of instructions, JSON-escaped, fit easily
MAX_CONVERSATIONS = 50  # listed on a project's page


def available(assistant, s) -> bool:
    """May this web session use projects: its own memory store, the
    project file tools in its registry, and a chat that can be put in one?"""
    registry = getattr(s, "registry", None)
    find = getattr(assistant, "memory_store_of", None)
    return bool(registry is not None and registry.has("search_project_files")
                and callable(getattr(s.chat, "set_project", None))
                and callable(find) and find(s) is not None)


def public_project(p: dict) -> dict:
    return {k: p[k] for k in ("id", "name", "instructions", "course", "created",
                              "updated", "memories", "files")}


def public_file(f: dict) -> dict:
    return {k: f[k] for k in ("id", "name", "kind", "size", "pages", "created")}


def public_memory(m: dict) -> dict:
    return {k: m[k] for k in ("id", "text", "created", "untrusted")}


def register(app, assistant, session_of, error) -> None:
    from fastapi import Request
    from starlette.concurrency import run_in_threadpool

    from kwil import projects
    from kwil.attachments import AttachmentRejected
    from kwil.memory_store import (MAX_INSTRUCTIONS, MAX_PROJECT_MEMORIES,
                                   MAX_PROJECT_NAME, MAX_PROJECTS, FileRefused,
                                   NoSuchProject, ProjectsFull, valid_file_id,
                                   valid_project_id)

    def bounds() -> dict:
        return getattr(assistant, "project_limits", None) or projects.limits({})

    def guard(request):
        """(session, their store, error response)."""
        s = session_of(request)
        if s is None:
            return None, None, error("not signed in", 401)
        if not available(assistant, s):
            return None, None, error("not found", 404)
        return s, assistant.memory_store_of(s), None

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
        log.exception("Projects: %s failed", what)
        return error("couldn't reach your projects; try again", 500)

    def limits(store) -> dict:
        b = bounds()
        return {"projects": MAX_PROJECTS, "name": MAX_PROJECT_NAME,
                "instructions": MAX_INSTRUCTIONS, "memories": MAX_PROJECT_MEMORIES,
                "files": b["max_files"], "file_mb": b["max_bytes"] / 1_000_000,
                "person_mb": b["max_total_bytes"] / 1_000_000,
                "used_mb": round(store.file_bytes() / 1_000_000, 2)}

    @app.get("/api/projects")
    async def projects_list(request: Request):
        s, store, err = guard(request)
        if err:
            return err
        try:
            found = await run_in_threadpool(store.projects)
            lim = await run_in_threadpool(limits, store)
        except Exception:
            return failed("listing")
        return {"projects": [public_project(p) for p in found], "limits": lim}

    @app.post("/api/projects")
    async def projects_create(request: Request):
        s, store, err = guard(request)
        if err:
            return err
        body = await body_of(request)
        if body is None:
            return error("bad request", 400)
        name, instructions = body.get("name"), body.get("instructions", "")
        course = body.get("course", "")
        if not all(isinstance(v, str) for v in (name, instructions, course)):
            return error("bad request", 400)
        try:
            made = await run_in_threadpool(store.create_project, name, instructions,
                                           projects.course_key(course) if course else "")
        except ProjectsFull:
            return error(f"You have {MAX_PROJECTS} projects: delete one first", 409)
        except ValueError as e:
            return error(str(e), 400)
        except Exception:
            return failed("creating")
        return {"ok": True, "project": public_project(made)}

    @app.get("/api/projects/suggestions")
    async def projects_suggestions(request: Request):
        s, store, err = guard(request)
        if err:
            return err
        try:
            existing = await run_in_threadpool(store.projects)
        except Exception:
            return failed("listing")
        canvas, deadlines, notion = await run_in_threadpool(_courses, request.app,
                                                            assistant, s)
        found = projects.suggestions(existing, canvas=canvas, deadlines=deadlines,
                                     notion=notion)
        log.info("Projects: %d suggestion(s) (%d Canvas, %d deadline, %d Notion course "
                 "names)", len(found), len(canvas), len(deadlines), len(notion))
        return {"suggestions": found}

    def own(store, project_id: str):
        return store.project(project_id) if valid_project_id(project_id) else None

    @app.get("/api/projects/{project_id}")
    async def projects_show(project_id: str, request: Request):
        s, store, err = guard(request)
        if err:
            return err
        try:
            found = await run_in_threadpool(own, store, project_id)
            if found is None:
                return error("not found", 404)
            files = await run_in_threadpool(store.files, project_id)
            memories = await run_in_threadpool(store.list, project_id)
            paused = await run_in_threadpool(lambda: store.paused)
        except Exception:
            return failed("reading")
        conversations = []
        history = assistant.history_of(s)[0]
        if history is not None:
            from kwil import history_web

            try:
                conversations = [history_web.item(c) for c in await run_in_threadpool(
                    lambda: history.list(limit=MAX_CONVERSATIONS, archived=None,
                                         project=project_id))]
            except Exception:
                log.exception("Projects: listing a project's conversations failed")
        return {"project": public_project(found), "files": [public_file(f) for f in files],
                "memories": [public_memory(m) for m in memories], "paused": paused,
                "conversations": conversations, "history": history is not None,
                "active": getattr(s.chat, "project_id", None) == project_id}

    @app.post("/api/projects/{project_id}/update")
    async def projects_update(project_id: str, request: Request):
        s, store, err = guard(request)
        if err:
            return err
        if not valid_project_id(project_id):
            return error("not found", 404)
        body = await body_of(request)
        if body is None or not body or not set(body) <= {"name", "instructions"} \
                or not all(isinstance(v, str) for v in body.values()):
            return error("bad request", 400)
        try:
            saved = await run_in_threadpool(lambda: store.update_project(
                project_id, name=body.get("name"), instructions=body.get("instructions")))
        except ValueError as e:
            return error(str(e), 400)
        except Exception:
            return failed("saving")
        if saved is None:
            return error("not found", 404)
        return {"ok": True, "project": public_project(saved)}

    @app.post("/api/projects/{project_id}/delete")
    async def projects_delete(project_id: str, request: Request):
        s, store, err = guard(request)
        if err:
            return err
        if not valid_project_id(project_id):
            return error("not found", 404)
        body = await body_of(request)
        if body is None or body.get("confirm") is not True:
            return error("bad request", 400)
        try:
            found = await run_in_threadpool(store.delete_project, project_id)
        except Exception:
            return failed("deleting")
        if not found:
            return error("not found", 404)
        history = assistant.history_of(s)[0]
        if history is not None:  # its conversations stay, in no project
            try:
                await run_in_threadpool(history.unfile_project, project_id)
            except Exception:
                log.exception("Projects: unfiling a deleted project's conversations failed")
        return {"ok": True}

    @app.post("/api/projects/{project_id}/files")
    async def projects_upload(project_id: str, request: Request):
        s, store, err = guard(request)
        if err:
            return err
        if not valid_project_id(project_id):
            return error("not found", 404)
        if not request.headers.get("content-type", "").startswith("multipart/form-data"):
            return error("send a form with one file", 415)
        b = bounds()
        try:
            form = await request.form(max_files=1, max_fields=1)
        except Exception:  # noqa: BLE001 - any malformed body, or two files
            return error("bad request (one file at a time)", 400)
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            return error("send a form with one file", 400)
        data = await upload.read(b["max_bytes"] + 1)
        try:
            kind, name, text, pages = await run_in_threadpool(
                projects.prepare_file, data, upload.filename, max_bytes=b["max_bytes"])
            saved = await run_in_threadpool(lambda: store.add_file(
                project_id, name, kind, data, text=text, pages=pages,
                max_files=b["max_files"], max_bytes=b["max_bytes"],
                max_total_bytes=b["max_total_bytes"]))
        except (AttachmentRejected, FileRefused) as e:
            log.info("Projects: a file for %s was refused (%d bytes)", project_id, len(data))
            return error(str(e), 400 if isinstance(e, AttachmentRejected) else 409)
        except NoSuchProject:
            return error("not found", 404)
        except Exception:
            return failed("adding a file")
        return {"ok": True, "file": public_file(saved)}

    @app.post("/api/projects/{project_id}/files/{file_id}/delete")
    async def projects_file_delete(project_id: str, file_id: str, request: Request):
        s, store, err = guard(request)
        if err:
            return err
        if not valid_project_id(project_id) or not valid_file_id(file_id):
            return error("not found", 404)
        try:
            found = await run_in_threadpool(store.file, file_id)
            if found is None or found["project"] != project_id:
                return error("not found", 404)
            await run_in_threadpool(store.delete_file, file_id)
        except Exception:
            return failed("deleting a file")
        return {"ok": True}


def _courses(app, assistant, s) -> tuple[list[str], list[str], list[str]]:
    """This person's course names: (Canvas course content, deadlines,
    Notion Course options: the owner's only). A source that fails is
    skipped (logged by error type only)."""
    uid = s.user
    canvas, deadlines, notion = [], [], []
    sync = getattr(app.state, "student", None)
    store = getattr(sync, "courses", None)
    if store is not None:
        try:
            canvas = [c["course"] for c in store.stats(uid)["courses"] if c.get("course")]
        except Exception as e:
            log.warning("Projects: Canvas courses couldn't be read (%s)", type(e).__name__)
    dl = getattr(assistant, "deadlines", None)
    if dl is not None:
        try:
            deadlines = [i["course"] for i in dl.list(uid) if i.get("course")]
        except Exception as e:
            log.warning("Projects: deadline courses couldn't be read (%s)",
                        type(e).__name__)
    fetch = getattr(assistant, "notion_courses", None)
    if uid is None and callable(fetch):
        try:
            notion = [str(n) for n in fetch() or ()]
        except Exception as e:
            log.warning("Projects: Notion courses couldn't be read (%s)", type(e).__name__)
    return canvas, deadlines, notion
