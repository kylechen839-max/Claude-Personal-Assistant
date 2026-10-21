"""The phone app's school deadlines: connect Canvas, sync, and the "What I
captured" list (web/student.js), for the owner and every invited person,
each on their own deadlines (deadlines.py).

  GET  /api/student/items              {items, canvas: status} (the person's own)
  POST /api/student/canvas             {ics_url} or {base_url, token}: save their
                                       Canvas link (invited people; the owner's is
                                       CANVAS_ICS_URL / CANVAS_TOKEN in the env)
  POST /api/student/disconnect         {delete_items?}: forget the link (and,
                                       asked, what came from Canvas)
  POST /api/student/sync               fetch now: at most every [student]
                                       sync_minutes per person, one at a time
  POST /api/student/items/{id}/resolve {choice: "confirm" | "dismiss" | a date}
  DELETE /api/student/items/{id}       delete one (also POST .../{id}/delete)
  DELETE /api/student/source/{source}  delete all from one source (also POST
                                       .../{source}/delete: the failover
                                       preflight allows only GET and POST)
  GET  /api/student/schools?q=         Canvas's school lookup (canvas.find_school)

All need a signed-in session (webapp.require_session); each person only
ever reaches their own file, so there is no id of a person in any path.
With [student] off they are 404.

An invited person's link is stored with userkeys.seal under
canvas.CANVAS_LABEL, bound to their id, in `<[family] dir>/<id>/canvas.enc`
(google_auth.write_private), deleted with their folder. Without a usable
USER_DATA_KEY nothing is stored (503). The feed URL is itself a secret
(it reads their calendar without a sign-in) and is treated like the token:
never logged, never returned (the status says only which kind and the
school's host). A school password is never asked for or stored.

Sync (StudentSync.sync, also callable by a scheduler): the calendar feed
(source "ics") or the planner over the API ("canvas_api"; module pages
too with [canvas] pages, "modules"), then, with [student] gmail and their
Gmail connected, Canvas's notification emails from the last 14 days
("gmail", read-only, each email read once). Claude reads only emails and
pages (canvas.extract_from_pages), on the person's own Claude key when
they have one (family.UserApi), with [student] extract_model. Logs: ids,
counts, source names and error codes only.
"""

# No `from __future__ import annotations` here: FastAPI must see the real
# Request class in the route signatures (it is imported inside register()).
import logging
import os
import re
import threading
import time
from collections import OrderedDict, deque
from pathlib import Path
from urllib.parse import urlsplit

from assistant import canvas
from assistant.deadlines import SOURCES, DeadlineError

log = logging.getLogger(__name__)

CANVAS_FILE = "canvas.enc"      # in <[family] dir>/<id>/
OWNER_ICS_ENV = "CANVAS_ICS_URL"
OWNER_TOKEN_ENV = "CANVAS_TOKEN"
MAX_TOKEN = 200
_TOKEN = re.compile(r"^[A-Za-z0-9~_.-]{20,200}$")
MAX_BODY = 8192                 # bytes of a request body
SCHOOL_LOOKUPS = 10             # per person per minute
MAX_PEOPLE = 1024               # entries in the throttle and lock maps
CANVAS_SOURCES = ("ics", "canvas_api", "modules")


class SyncBusy(Exception):
    """A sync for this person is running, or ran too recently (`wait`
    seconds to go; 0: one is running)."""

    def __init__(self, wait: float = 0):
        super().__init__("busy")
        self.wait = wait


class StudentSync:
    """Connecting and syncing, without HTTP. `uid` None is the owner."""

    def __init__(self, store, config: dict, family=None, environ=None,
                 clock=time.monotonic):
        self.store = store
        self.config = config
        self.family = family
        self.environ = environ        # None: os.environ, read on every call
        self.clock = clock
        self.lock = threading.Lock()
        self._running: OrderedDict[str, threading.Lock] = OrderedDict()
        self._last: OrderedDict[str, float] = OrderedDict()
        self._lookups: OrderedDict[str, deque] = OrderedDict()
        self._client = None           # the owner's Anthropic client (no family)

    @property
    def env(self):
        return os.environ if self.environ is None else self.environ

    # ---------- whose ----------
    def listed(self, uid) -> bool:
        return uid is None or (self.family is not None and self.family.get(uid) is not None)

    def timezone(self, uid):
        from assistant.tools.timeutil import local_zone

        name = ""
        if uid is not None and self.family is not None:
            name = (self.family.get(uid) or {}).get("timezone") or ""
        try:
            return local_zone(name or self.config["assistant"]["timezone"] or None)
        except Exception:  # a zone this machine doesn't know
            return local_zone(None)

    def _file(self, uid) -> Path:
        return Path(self.config["family"]["dir"]) / uid / CANVAS_FILE

    def _keys_env(self):
        return self.family.keys.environ if self.family is not None else self.environ

    # ---------- their Canvas link ----------
    def credentials(self, uid) -> dict | None:
        """{kind: "ics", ics_url} or {kind: "api", base_url, token}, or None."""
        if uid is None:
            ics = self.env.get(OWNER_ICS_ENV, "").strip()
            token = self.env.get(OWNER_TOKEN_ENV, "").strip()
            base = self.config["canvas"]["base_url"].strip()
            if token and base:
                return {"kind": "api", "base_url": base, "token": token}
            if ics:
                return {"kind": "ics", "ics_url": ics}
            return None
        from assistant.userkeys import unseal

        try:
            raw = self._file(uid).read_bytes()
        except FileNotFoundError:
            return None
        except OSError as e:
            log.warning("Student: %s's Canvas file is unreadable (%s)", uid, type(e).__name__)
            return None
        data = unseal(canvas.CANVAS_LABEL, uid, raw, self._keys_env(), what="Canvas link",
                      redo="connect Canvas again")
        if data is None or data.get("kind") not in ("ics", "api"):
            return None
        return data

    def status(self, uid) -> dict:
        """What the card shows; never the feed URL or the token."""
        from assistant import userkeys

        found = self.credentials(uid)
        school = None
        if found is not None:
            school = urlsplit(found.get("base_url") or found.get("ics_url") or "").hostname
        return {"connected": found is not None,
                "kind": found["kind"] if found else None, "school": school,
                "managed": "env" if uid is None else "app",
                "can_store": uid is None or userkeys.secret_problem(self._keys_env()) is None,
                "gmail": bool(self.config["student"]["gmail"]) and self._inbox(uid) is not None,
                "last_sync": self.store.last_sync(uid)}

    @staticmethod
    def clean_link(body: dict) -> dict:
        """The record to seal from a connect request; ValueError (safe to
        show) when it isn't a usable feed link or school + token. Checks
        the address is public (canvas.public_url), so it resolves DNS."""
        if body.get("ics_url"):
            url = canvas.public_url(str(body["ics_url"]), path_hint="/feeds/calendars/")
            return {"kind": "ics", "ics_url": url}
        token = str(body.get("token") or "").strip()
        if not body.get("base_url") or not token:
            raise ValueError("paste your Canvas calendar feed link, or your school's "
                             "Canvas address and an access token")
        if len(token) > MAX_TOKEN or not _TOKEN.match(token):
            raise ValueError("that doesn't look like a Canvas access token")
        return {"kind": "api", "base_url": canvas.base_url(str(body["base_url"])),
                "token": token}

    def connect(self, uid: str, record: dict) -> bool:
        """Seal and store an invited person's link (replacing any). False
        if they were removed meanwhile; userkeys.KeyProblem 503 without
        USER_DATA_KEY (nothing written)."""
        from assistant.tools.google_auth import write_private
        from assistant.userkeys import seal

        text = seal(canvas.CANVAS_LABEL, uid, record, self._keys_env())
        with self.family.while_listed(uid) as listed:
            if not listed:
                return False
            path = self._file(uid)
            path.parent.mkdir(parents=True, exist_ok=True)
            write_private(path, text)
        with self.lock:  # a new link may sync at once
            self._last.pop(uid, None)
        log.info("Student: %s connected Canvas (%s)", uid, record["kind"])
        return True

    def disconnect(self, uid: str, delete_items: bool = False) -> dict:
        from assistant.family import _unlink

        path = self._file(uid)
        had = path.exists()
        _unlink(path)
        deleted = 0
        if delete_items:
            for source in CANVAS_SOURCES:
                deleted += self.store.delete_source(uid, source)
        log.info("Student: %s disconnected Canvas (%d item(s) deleted)", uid, deleted)
        return {"connected": False, "had": had, "deleted": deleted}

    # ---------- syncing ----------
    def _inbox(self, uid):
        if uid is None:
            from assistant.cli import owner_gmail

            return owner_gmail(self.config, output_fn=lambda *_: None)
        user = self.family.get(uid) if self.family is not None else None
        return self.family.gmail_inbox(user) if user is not None else None

    def _claude(self, uid):
        """(client, meter) for the extraction call: an invited person's
        own key (or, family tier, the owner's), else the owner's."""
        if uid is not None:
            from assistant.family import UserApi

            api = UserApi(self.family, uid)
            return api, api
        if self.family is not None:
            return self.family.client(None), None
        if self._client is None:
            import anthropic

            a = self.config["assistant"]
            self._client = anthropic.Anthropic(max_retries=a["api_max_retries"],
                                               timeout=a["api_timeout"])
        return self._client, None

    def _model(self) -> str:
        a = self.config["assistant"]
        return self.config["student"]["extract_model"] or a["fast_model"] or a["model"]

    def _extract(self, uid, source: str, texts: list, tz) -> list:
        """Claude reads the texts not read before; each is then marked read."""
        keys = set(self.store.unseen(uid, source, [x[0] for x in texts]))
        fresh = [x for x in texts if x[0] in keys]  # (key, text[, sent_at])
        if not fresh:
            return []
        client, meter = self._claude(uid)
        found = canvas.extract_from_pages(client, self._model(), fresh, tz=tz, meter=meter)
        self.store.mark_seen(uid, source, [x[0] for x in fresh])
        return found

    def sync(self, uid) -> dict:
        """Read every source the person has; {sources: {source: counts},
        errors: {source: code}}. Raises KeyError if they were removed."""
        from datetime import datetime, timezone

        tz, now = self.timezone(uid), datetime.now(timezone.utc)
        # the whole sync's time (each request's timeout is per read only)
        deadline = self.clock() + canvas.SYNC_SECONDS
        out, errors = {}, {}
        found = self.credentials(uid)
        c = self.config["canvas"]

        def run(source, fetch):
            try:
                out[source] = self.store.propose(uid, fetch(), source, tz=tz)
            except KeyError:
                raise
            except canvas.CanvasError as e:
                errors[source] = str(e)
            except Exception as e:  # never the message: it can hold a URL or text
                errors[source] = _code(e)
            if source in errors:
                log.warning("Student: %s's %s sync failed (%s)", uid or "the owner",
                            source, errors[source])

        if found is not None and found["kind"] == "ics":
            def ics():
                with canvas.http_client(c["timeout"]) as http:
                    data = canvas.fetch_ics(found["ics_url"], http, deadline=deadline,
                                            clock=self.clock)
                return canvas.upcoming(canvas.parse_ics(data, tz), now)
            run("ics", ics)
        elif found is not None:
            api = None

            def planner():
                nonlocal api
                base = canvas.base_url(found["base_url"])  # checked again: DNS can change
                api = canvas.CanvasAPI(base, found["token"], timeout=c["timeout"],
                                       deadline=deadline, clock=self.clock)
                return canvas.upcoming(api.deadlines(now), now)
            try:
                run("canvas_api", planner)
                if c["pages"] and api is not None and "canvas_api" not in errors:
                    run("modules", lambda: canvas.upcoming(self._extract(
                        uid, "modules", api.page_texts(int(c["max_pages"])), tz), now))
            finally:
                if api is not None:
                    api.close()
        if self.config["student"]["gmail"]:
            inbox = self._inbox(uid)
            if inbox is not None and self.clock() > deadline:
                errors["gmail"] = "timed_out"  # the Canvas step used the time up
            elif inbox is not None:
                run("gmail", lambda: canvas.upcoming(self._extract(
                    uid, "gmail", canvas.email_texts(inbox.search(canvas.GMAIL_QUERY, 20)),
                    tz), now))
        self.store.note_sync(uid)
        log.info("Student: synced %s (%d source(s), %d failed)", uid or "the owner",
                 len(out), len(errors))
        return {"sources": out, "errors": errors}

    def try_sync(self, uid) -> dict:
        """sync(), unless one is running for them (SyncBusy(0)) or the last
        one started less than [student] sync_minutes ago (SyncBusy(wait))."""
        key, gap = uid or "", float(self.config["student"]["sync_minutes"]) * 60
        with self.lock:
            last = self._last.get(key)
            if last is not None and self.clock() - last < gap:
                raise SyncBusy(gap - (self.clock() - last))
            running = self._running.get(key)
            if running is None:
                running = self._running[key] = threading.Lock()
            self._running.move_to_end(key)
            if not running.acquire(blocking=False):
                raise SyncBusy(0)
            self._last[key] = self.clock()
            self._last.move_to_end(key)
            _trim(self._last)
            idle = [k for k, lock in self._running.items() if not lock.locked()]
            for k in idle[:max(0, len(self._running) - MAX_PEOPLE)]:
                del self._running[k]
        try:
            return self.sync(uid)
        finally:
            running.release()

    def lookup_allowed(self, uid) -> bool:
        """At most SCHOOL_LOOKUPS school searches a minute per person."""
        key, now = uid or "", self.clock()
        with self.lock:
            recent = self._lookups.setdefault(key, deque())
            self._lookups.move_to_end(key)
            while recent and now - recent[0] > 60:
                recent.popleft()
            if len(recent) >= SCHOOL_LOOKUPS:
                return False
            recent.append(now)
            _trim(self._lookups)
            return True


def _trim(mapping: OrderedDict) -> None:
    while len(mapping) > MAX_PEOPLE:
        mapping.popitem(last=False)


def _code(error: Exception) -> str:
    """A short, safe error code for a failed source."""
    name = type(error).__name__
    if name in ("MissingKey",):
        return "no_key"
    if name in ("MissingScopes", "GoogleSignInError", "RefreshError"):
        return "reconnect"
    return name


def register(app, family, web, error) -> None:
    """Add the routes to a webapp.create_app() app (shaped like
    notion_web.register). The store is Family's, else the process's
    (deadlines.current(), opened by cli.register_student)."""
    from fastapi import Depends, Request
    from fastapi.responses import JSONResponse
    from starlette.concurrency import run_in_threadpool

    from assistant import deadlines, userkeys, webapp

    store = family.deadlines if family is not None else deadlines.current()
    config = family.config if family is not None else (store.config if store else None)
    if store is None or config is None or not config.get("student", {}).get("enabled"):
        sync = None
    else:
        sync = StudentSync(store, config, family)
    app.state.student = sync  # tests, and a scheduler

    def person(session):
        """(True, their id or None for the owner), or (False, None): off,
        or someone no longer listed."""
        if sync is None or not sync.listed(session.user):
            return False, None
        return True, session.user

    async def body_of(request: Request) -> dict:
        raw = await request.body()
        if len(raw) > MAX_BODY:
            return {}
        try:
            import json

            body = json.loads(raw or b"{}")
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}

    def nostore(data, status=200):
        return JSONResponse(data, status, headers={"Cache-Control": "no-store"})

    @app.get("/api/student/items")
    async def student_items(session=Depends(webapp.require_session)):
        ok, uid = person(session)
        if not ok:
            return error("not found", 404)
        items = await run_in_threadpool(store.list, uid)
        status = await run_in_threadpool(sync.status, uid)
        return nostore({"items": items, "canvas": status})

    @app.post("/api/student/canvas")
    async def student_connect(request: Request, session=Depends(webapp.require_session)):
        ok, uid = person(session)
        if not ok:
            return error("not found", 404)
        if uid is None:
            return error(f"on your own server, put {OWNER_ICS_ENV} (or {OWNER_TOKEN_ENV} "
                         "and [canvas] base_url) in its environment", 409)
        if userkeys.secret_problem(family.keys.environ) is not None:  # fail closed
            log.warning("Student: can't store %s's Canvas link (%s isn't usable)",
                        uid, userkeys.ENV)
            return error("this server can't store sign-ins yet (its owner needs "
                         f"to set {userkeys.ENV})", 503)
        body = await body_of(request)
        try:
            record = await run_in_threadpool(StudentSync.clean_link, body)
        except ValueError as e:
            return error(str(e), 400)
        try:
            saved = await run_in_threadpool(sync.connect, uid, record)
        except userkeys.KeyProblem as e:
            return error(str(e), e.status)
        except OSError:
            return error("couldn't save the Canvas link; try again", 500)
        if not saved:
            return error("not found", 404)
        return nostore(await run_in_threadpool(sync.status, uid))

    @app.post("/api/student/disconnect")
    async def student_disconnect(request: Request, session=Depends(webapp.require_session)):
        ok, uid = person(session)
        if not ok:
            return error("not found", 404)
        if uid is None:
            return error(f"on your own server, remove {OWNER_ICS_ENV} / {OWNER_TOKEN_ENV} "
                         "from its environment", 409)
        body = await body_of(request)
        try:
            return nostore(await run_in_threadpool(sync.disconnect, uid,
                                                   bool(body.get("delete_items"))))
        except (OSError, KeyError):
            return error("couldn't disconnect Canvas; try again", 500)

    @app.post("/api/student/sync")
    async def student_sync(session=Depends(webapp.require_session)):
        ok, uid = person(session)
        if not ok:
            return error("not found", 404)
        try:
            result = await run_in_threadpool(sync.try_sync, uid)
        except SyncBusy as e:
            wait = int(e.wait) + 1 if e.wait else 0
            response = error("a sync is already running" if not wait else
                             f"synced recently; try again in {wait // 60 + 1} minute(s)",
                             409 if not wait else 429)
            if wait:
                response.headers["Retry-After"] = str(wait)
            return response
        except KeyError:
            return error("not found", 404)
        return nostore(result)

    def parse_id(item_id: str):
        return int(item_id) if re.fullmatch(r"\d{1,12}", item_id or "") else None

    @app.post("/api/student/items/{item_id}/resolve")
    async def student_resolve(item_id: str, request: Request,
                              session=Depends(webapp.require_session)):
        ok, uid = person(session)
        rid = parse_id(item_id)
        if not ok or rid is None:
            return error("not found", 404)
        body = await body_of(request)
        try:
            row = await run_in_threadpool(store.resolve, uid, rid,
                                          str(body.get("choice") or ""),
                                          sync.timezone(uid))
        except DeadlineError as e:
            return error(str(e), 400)
        except KeyError:
            return error("not found", 404)
        if row is None:
            return error("not found", 404)
        return nostore({"item": row})

    async def delete_item(item_id: str, session):
        ok, uid = person(session)
        rid = parse_id(item_id)
        if not ok or rid is None:
            return error("not found", 404)
        try:
            gone = await run_in_threadpool(store.delete, uid, rid)
        except KeyError:
            gone = False
        if not gone:
            return error("not found", 404)
        return {"ok": True, "deleted": 1}

    @app.delete("/api/student/items/{item_id}")
    async def student_delete(item_id: str, session=Depends(webapp.require_session)):
        return await delete_item(item_id, session)

    @app.post("/api/student/items/{item_id}/delete")
    async def student_delete_post(item_id: str, session=Depends(webapp.require_session)):
        return await delete_item(item_id, session)

    async def delete_source(source: str, session):
        ok, uid = person(session)
        if not ok or source not in SOURCES:
            return error("not found", 404)
        try:
            gone = await run_in_threadpool(store.delete_source, uid, source)
        except KeyError:
            return error("not found", 404)
        return {"ok": True, "deleted": gone}

    @app.delete("/api/student/source/{source}")
    async def student_delete_source(source: str, session=Depends(webapp.require_session)):
        return await delete_source(source, session)

    @app.post("/api/student/source/{source}/delete")
    async def student_delete_source_post(source: str,
                                         session=Depends(webapp.require_session)):
        return await delete_source(source, session)

    @app.get("/api/student/schools")
    async def student_schools(q: str = "", session=Depends(webapp.require_session)):
        ok, uid = person(session)
        if not ok:
            return error("not found", 404)
        if not sync.lookup_allowed(uid):
            return error("too many searches; wait a minute", 429)
        return nostore({"schools": await run_in_threadpool(canvas.find_school, q)})
