"""The Canvas helper's way in: course content pushed from the person's own
signed-in Canvas tab (the Kwil Canvas helper, a Chrome/Edge extension; an
iOS Shortcut later), for schools that don't let students make an access
token. Kwil fetches nothing here: the helper reads Canvas's API with the
person's own sign-in and sends what it read; everything it sends is
checked again and goes through the token sync's pipeline
(coursefiles.sync_content, textextract, the same bounds, the deadline
drafts), into that person's own store.

  POST /api/student/canvas/helper         (signed in) a one-time pairing code,
                                          {code, expires_in}: valid PAIR_SECONDS,
                                          shown on the page, never in a URL
  POST /api/student/canvas/helper/revoke  (signed in) forget the helper's key
                                          (also DELETE /api/student/canvas/helper)
  POST /api/student/canvas/pair           {code} -> {key, ...}: the sync key,
                                          returned once (public; the code is
                                          the credential, wrong ones count
                                          toward the sign-in lockout)
  POST /api/student/canvas/push/manifest  (sync key) the courses as Canvas's API
                                          lists them (see clean_manifest);
                                          answers which files it wants
  POST /api/student/canvas/push/file      (sync key) multipart: course_id,
                                          file_id, file: one file it asked for

Sync keys (`kcs1.` + 32 random bytes, base64url) are per person (the owner
included) and are the only way into the two push routes, which never read a
cookie; a key opens nothing else (CloudGuard answers 401 to it anywhere
else). Only its SHA-256 is kept (family.hash_token, compared with
hmac.compare_digest), in `<[student] dir>/canvas_helper.json` for the owner
and `<[family] dir>/<id>/canvas_helper.json` for an invited person
(google_auth.write_private), with the pairing code's hash while one is
open. A person has one key: pairing again replaces it. "Revoke helper",
Disconnect and removing the person delete it; signing out doesn't (it is a
different credential, like a phone's own sign-in). Wrong keys and codes
count toward the web app's LoginLimiter per address (the right one always
works); a key gets PUSHES_PER_HOUR requests an hour.

The push routes accept a browser extension's Origin (webapp's cross-site
check, the failover edge and CloudGuard let exactly `push_request`s
through): no cookie is used, so there is nothing to forge, and they send no
CORS headers (an extension with host permission doesn't need them; a page
can't read the answer). Pairing is same-origin (the extension's content
script on the Kwil page reads the code from the card's `data-kwil-pair`
element, web/student.js, and trades it) or from a non-browser client (a
Shortcut); no cross-site exemption exists for it.

What the helper sends is someone else's text (teachers, Canvas): the same
untrusted store as the token sync's, origin "helper". A manifest only ever
writes the paired person's own store, keyed by the course ids it names;
course ids of other people don't exist there. A file is accepted only if a
manifest of that person asked for it within WANTED_SECONDS, with the
metadata that manifest sent; a Disconnect or revoke after the manifest
(generation or key changed) makes it write nothing. Logs: ids, counts and
codes, never a title, text, key or code.
"""

# No `from __future__ import annotations` here: FastAPI must see the real
# Request class in the route signatures (it is imported inside register()).
import hmac
import json
import logging
import re
import secrets
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

KEY_PREFIX = "kcs1."
CODE_PREFIX = "kcp1."
_KEY = re.compile(r"^kcs1\.[A-Za-z0-9_-]{43}$")     # secrets.token_urlsafe(32)
_CODE = re.compile(r"^kcp1\.[A-Za-z0-9_-]{32}$")    # secrets.token_urlsafe(24)
HELPER_FILE = "canvas_helper.json"
PAIR_SECONDS = 600              # a pairing code works this long, once
PAIR_PATH = "/api/student/canvas/pair"
MANIFEST_PATH = "/api/student/canvas/push/manifest"
FILE_PATH = "/api/student/canvas/push/file"
PUSH_PATHS = frozenset({MANIFEST_PATH, FILE_PATH})
PUSHES_PER_HOUR = 120           # push requests per key (a run: a manifest + max_files files)
WANTED_SECONDS = 3600           # a file asked for must arrive within this
MAX_WANTED = 200                # files asked for and not yet pushed, per person
MAX_COURSES = 100               # courses in one manifest
MAX_LISTING = 1000              # items in one listing (as canvas.CanvasAPI.get)
MAX_ID = 10 ** 15
MAX_NAME = 300
MAX_PAIR_BODY = 4096
LISTINGS = ("pages", "assignments", "modules", "files")
# each listing's HTML field, which sync_content reads as text (else None)
_HTML = {"pages": "body", "assignments": "description", "announcements": "message"}
UPLOAD_MARGIN = 65_536          # multipart overhead under [webapp] max_upload_mb
_NOBODY = object()


def is_key(text) -> bool:
    """Shaped like a sync key (`kcs1.` + 43 base64url characters)."""
    return isinstance(text, str) and bool(_KEY.match(text.strip()))


def push_request(method: str, path: str, authorization: str | None) -> bool:
    """A helper's push: POST to a push route with a `kcs1.` bearer. The
    outer layers let exactly these through (cross-site, CORS-less); the
    routes check the key."""
    auth = authorization or ""
    return (method == "POST" and path in PUSH_PATHS and auth[:7].lower() == "bearer "
            and auth[7:].strip().startswith(KEY_PREFIX))


def _who(uid) -> str:
    return uid or "the owner"


def _same(stored, digest: str) -> bool:
    """hmac.compare_digest for what a file held (maybe not a string)."""
    stored = stored if isinstance(stored, str) and stored.isascii() else ""
    return hmac.compare_digest(stored, digest)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _id(value) -> int | None:
    """A Canvas id (an int or its digits), else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and re.fullmatch(r"\d{1,16}", value.strip()):
        value = int(value.strip())
    return value if isinstance(value, int) and 0 < value <= MAX_ID else None


class HelperKeys:
    """Each person's sync key and open pairing code, hashed, in their own
    canvas_helper.json. `uid` None is the owner."""

    def __init__(self, config: dict, family=None, clock=time.time):
        self.config = config
        self.family = family
        self.clock = clock
        self.lock = threading.Lock()

    def path(self, uid) -> Path:
        if uid is None:
            return Path(self.config["student"]["dir"]) / HELPER_FILE
        from kwil.family import valid_id

        if not valid_id(uid):
            raise KeyError("not a person")
        return Path(self.config["family"]["dir"]) / uid / HELPER_FILE

    def people(self) -> list:
        from kwil.student_web import people

        return people(self.family)

    def _read(self, uid) -> dict:
        try:
            data = json.loads(self.path(uid).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            log.warning("Canvas helper: %s's key file is unreadable (%s)", _who(uid),
                        type(e).__name__)
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, uid, data: dict) -> bool:
        """Write privately and atomically; an invited person's only while
        listed (False if they were removed)."""
        from kwil.tools.google_auth import write_private

        def write():
            path = self.path(uid)
            path.parent.mkdir(parents=True, exist_ok=True)
            write_private(path, json.dumps(data))

        if uid is None:
            write()
            return True
        with self.family.while_listed(uid) as listed:
            if not listed:
                return False
            write()
            return True

    def start_pairing(self, uid) -> str | None:
        """A new one-time pairing code (replacing an open one; the key, if
        any, works until a pairing replaces it). None if `uid` is gone."""
        code = CODE_PREFIX + secrets.token_urlsafe(24)
        from kwil.family import hash_token

        with self.lock:
            data = self._read(uid)
            data.update(pair_hash=hash_token(code), pair_expires=self.clock() + PAIR_SECONDS)
            if not self._write(uid, data):
                return None
        log.info("Canvas helper: %s opened a pairing code", _who(uid))
        return code

    def pair(self, code) -> tuple | None:
        """(uid, the new key) for an open, unexpired code, which is used up;
        None for anything else."""
        from kwil.family import hash_token

        if not isinstance(code, str) or not _CODE.match(code.strip()):
            return None
        digest, now = hash_token(code), self.clock()
        with self.lock:
            found = None
            for uid in self.people():  # compare every one, in constant time
                data = self._read(uid)
                if _same(data.get("pair_hash"), digest):
                    found = (uid, data)
            if found is None:
                return None
            uid, data = found
            try:
                expires = float(data.get("pair_expires") or 0)
            except (TypeError, ValueError):
                expires = 0
            if expires < now:
                return None
            key = KEY_PREFIX + secrets.token_urlsafe(32)
            if not self._write(uid, {"key_hash": hash_token(key), "paired_at": _iso(now)}):
                return None
        log.info("Canvas helper: %s paired a helper", _who(uid))
        return uid, key

    def verify(self, key) -> tuple | None:
        """(uid, the key's hash) for a current key, else None."""
        from kwil.family import hash_token

        if not isinstance(key, str) or not _KEY.match(key.strip()):
            return None
        digest = hash_token(key)
        found = _NOBODY  # the owner's id is None
        for uid in self.people():  # compare every one, in constant time
            if _same(self._read(uid).get("key_hash"), digest):
                found = uid
        return None if found is _NOBODY else (found, digest)

    def current(self, uid, digest: str) -> bool:
        """Is this still the person's key (not revoked or replaced)?"""
        return _same(self._read(uid).get("key_hash"), digest)

    def status(self, uid) -> dict:
        data = self._read(uid)
        try:
            pairing = bool(data.get("pair_hash")) and \
                float(data.get("pair_expires") or 0) > self.clock()
        except (TypeError, ValueError):
            pairing = False
        return {"paired": bool(data.get("key_hash")), "pairing": pairing,
                "paired_at": data.get("paired_at") if data.get("key_hash") else None}

    def revoke(self, uid) -> bool:
        """Delete the person's key (and any open code). True if there was one."""
        from kwil.family import _unlink

        path = self.path(uid)
        with self.lock:
            had = bool(self._read(uid).get("key_hash"))
            _unlink(path)
        if had:
            log.info("Canvas helper: %s's helper key revoked", _who(uid))
        return had


# ---------------------------------------------------------------- the content
def clean_manifest(body) -> dict:
    """{courses: [...], announcements: [...] | None} from a manifest, or
    ValueError (safe to show). The shape is Canvas's own API answers, as
    the helper read them with the person's sign-in:

      {"courses": [{"id", "name", "course_code", "syllabus_body",
                    "pages": [...], "assignments": [...], "modules": [...],
                    "files": [...]}, ...],          # at most MAX_COURSES
       "announcements": [...]}                      # /api/v1/announcements

    A listing missing or null couldn't be read (nothing of it is dropped);
    a list is all of it (what it no longer has is dropped). A file's `url`
    is never kept: Kwil fetches nothing, the helper pushes the bytes."""
    if not isinstance(body, dict) or not isinstance(body.get("courses"), list):
        raise ValueError("send {courses: [...]}")
    if len(body["courses"]) > MAX_COURSES:
        raise ValueError(f"at most {MAX_COURSES} courses at a time")

    def listing(value, name: str):
        if value is None:
            return None
        if not isinstance(value, list) or len(value) > MAX_LISTING:
            raise ValueError(f"a listing is a list of at most {MAX_LISTING}")
        out = []
        for x in value:
            if not isinstance(x, dict):
                continue
            x = dict(x)
            html = _HTML.get(name)
            if html in x and not isinstance(x[html], str):
                x[html] = None          # not text: as if Canvas sent no body
            if name == "modules":       # a module's items: a list of objects, or none
                items = x.get("items")
                x["items"] = [i for i in items[:MAX_LISTING] if isinstance(i, dict)] \
                    if isinstance(items, list) else []
            out.append(x)
        return out

    courses, seen = [], set()
    for c in body["courses"]:
        cid = _id(c.get("id")) if isinstance(c, dict) else None
        if cid is None:
            raise ValueError("each course needs its Canvas id")
        if cid in seen:
            continue
        seen.add(cid)
        syllabus = c.get("syllabus_body")
        course = {"id": cid, "name": str(c.get("name") or "")[:MAX_NAME],
                  "course_code": str(c.get("course_code") or "")[:MAX_NAME],
                  "syllabus_body": syllabus if isinstance(syllabus, str) else ""}
        for name in LISTINGS:
            course[name] = listing(c.get(name), name)
        if course["files"] is not None:
            course["files"] = [{**f, "id": _id(f.get("id"))} for f in course["files"]
                               if _id(f.get("id")) is not None]
        courses.append(course)
    return {"courses": courses,
            "announcements": listing(body.get("announcements"), "announcements")}


class PushedCanvas:
    """What the helper pushed, in canvas.CanvasAPI's shape, for
    coursefiles.sync_content. Nothing is fetched: a listing that wasn't
    sent is "unavailable" (that one listing is skipped), and a file to read
    is recorded in `wanted` (the helper pushes it next) instead of being
    downloaded."""

    def __init__(self, manifest: dict, deadline: float, clock):
        self.deadline = deadline
        self.clock = clock
        self._courses = {c["id"]: c for c in manifest["courses"]}
        self._news = manifest["announcements"]
        self._files: dict = {}
        self.wanted: list[dict] = []

    def courses(self, syllabus: bool = False) -> list[dict]:
        return [{"id": c["id"], "name": c["name"], "course_code": c["course_code"],
                 "syllabus_body": c["syllabus_body"]} for c in self._courses.values()]

    def _listing(self, course_id, name) -> list:
        from kwil.canvas import CanvasError

        found = (self._courses.get(int(course_id)) or {}).get(name)
        if found is None:
            raise CanvasError("unavailable")
        return found

    def pages(self, course_id) -> list:
        return self._listing(course_id, "pages")

    def page(self, course_id, slug):
        from kwil.canvas import CanvasError

        raise CanvasError("unavailable")  # the helper sends bodies; Kwil fetches none

    def assignments(self, course_id) -> list:
        return self._listing(course_id, "assignments")

    def modules(self, course_id) -> list:
        return self._listing(course_id, "modules")

    def files(self, course_id) -> list:
        cid = int(course_id)
        out = []
        for f in self._listing(cid, "files"):
            f = {**f, "url": f"pushed:{cid}:{f['id']}"}  # never the helper's URL
            self._files[(cid, f["id"])] = f
            out.append(f)
        return out

    def announcements(self, course_ids, start) -> list:
        from kwil.canvas import CanvasError

        if self._news is None:
            raise CanvasError("unavailable")
        out = []
        for n in self._news:
            try:
                posted = datetime.fromisoformat(
                    str(n.get("posted_at") or "").replace("Z", "+00:00")).date()
            except ValueError:
                posted = None
            if posted is None or posted >= start:
                out.append(n)
        return out

    def download(self, url: str, max_bytes: int) -> bytes:
        from kwil.canvas import CanvasError
        from kwil.coursefiles import file_version

        m = re.fullmatch(r"pushed:(\d+):(\d+)", url)
        f = self._files.get((int(m.group(1)), int(m.group(2)))) if m else None
        if f is None:
            raise CanvasError("unavailable")
        c = self._courses[int(m.group(1))]
        self.wanted.append({"course_id": int(m.group(1)), "file_id": f["id"],
                            "course": c["name"] or c["course_code"],
                            "title": str(f.get("display_name") or f.get("filename") or ""),
                            "version": file_version(f, None)})
        raise CanvasError("wanted")


class HelperPush:
    """Pushes into the person's store, one at a time per person; the files
    each manifest asked for are remembered (in memory, WANTED_SECONDS, at
    most MAX_WANTED a person) until they arrive."""

    def __init__(self, sync, course_sync=None, clock=time.monotonic):
        from kwil.cloud import RateLimiter
        from kwil.student_web import MAX_PEOPLE

        self.sync = sync                  # student_web.StudentSync
        self.keys: HelperKeys = sync.helper
        self.course_sync = course_sync    # student_web.CourseSync: reads the dates
        self.clock = clock
        self.lock = threading.Lock()
        self._wanted: OrderedDict = OrderedDict()   # uid key -> OrderedDict
        self._busy: OrderedDict = OrderedDict()     # uid key -> Lock
        self.rate = RateLimiter(PUSHES_PER_HOUR, clock=clock, max_tracked=MAX_PEOPLE,
                                window=3600)
        self.max_people = MAX_PEOPLE

    def enabled(self) -> bool:
        """[canvas] helper (read on every call)."""
        return bool(self.sync.config["canvas"].get("helper"))

    def max_file_bytes(self) -> int:
        c, w = self.sync.config["canvas"], self.sync.config["webapp"]
        return max(0, min(int(float(c["max_file_mb"]) * 1_000_000),
                          int(float(w["max_upload_mb"]) * 1_000_000) - UPLOAD_MARGIN))

    def _trim(self, table: OrderedDict) -> None:
        while len(table) > self.max_people:
            table.popitem(last=False)

    @contextmanager
    def _hold(self, uid):
        """One push at a time per person (student_web.SyncBusy otherwise)."""
        from kwil.student_web import SyncBusy

        key = uid or ""
        with self.lock:
            busy = self._busy.get(key)
            if busy is None:
                busy = self._busy[key] = threading.Lock()
            self._busy.move_to_end(key)
            if not busy.acquire(blocking=False):
                raise SyncBusy(0)
            idle = [k for k, lock in self._busy.items() if not lock.locked()]
            for k in idle[:max(0, len(self._busy) - self.max_people)]:
                del self._busy[k]
        try:
            yield
        finally:
            busy.release()

    def alive(self, uid, digest: str, generation):
        """For coursefiles' writes: true while nobody disconnected since the
        push began and the key is still the person's."""
        def alive() -> bool:
            return self.sync._generation_of(uid) == generation and \
                self.keys.current(uid, digest)
        return alive

    def forget(self, uid) -> None:
        with self.lock:
            self._wanted.pop(uid or "", None)

    def _dates(self, uid) -> None:
        if self.course_sync is not None:
            self.course_sync.request_dates(uid)

    def manifest(self, uid, digest: str, manifest: dict) -> dict:
        """Store a manifest's content; {counts, want: [{course_id, file_id}],
        max_file_bytes}. KeyError: `uid` was removed; SyncBusy: another
        push of theirs is running."""
        from kwil import coursefiles

        c = self.sync.config["canvas"]
        generation = self.sync._generation_of(uid)
        alive = self.alive(uid, digest, generation)
        limit = self.max_file_bytes()
        with self._hold(uid):
            api = PushedCanvas(manifest, self.clock() + float(c["content_seconds"]),
                               self.clock)
            counts = coursefiles.sync_content(api, self.sync.courses, uid,
                                              max_files=int(c["max_files"]),
                                              max_file_bytes=limit, alive=alive,
                                              origin="helper")
            wanted = api.wanted if alive() else []
            now, key = self.clock(), uid or ""
            with self.lock:
                table = self._wanted.setdefault(key, OrderedDict())
                self._wanted.move_to_end(key)
                for w in wanted:
                    table[(w["course_id"], w["file_id"])] = {
                        **w, "generation": generation, "at": now}
                    table.move_to_end((w["course_id"], w["file_id"]))
                while len(table) > MAX_WANTED:
                    table.popitem(last=False)
                self._trim(self._wanted)
        self._dates(uid)
        log.info("Canvas helper: %s pushed %d course(s): %d added, %d updated, "
                 "%d unchanged, %d removed, %d file(s) wanted", _who(uid), counts["courses"],
                 counts["added"], counts["updated"], counts["unchanged"], counts["removed"],
                 len(wanted))
        return {"counts": counts, "max_file_bytes": limit,
                "want": [{"course_id": w["course_id"], "file_id": w["file_id"]}
                         for w in wanted]}

    def file(self, uid, digest: str, course_id: int, file_id: int,
             data: bytes | None) -> dict:
        """Store one pushed file's text (`data` None: it was over
        max_file_bytes). LookupError: no manifest asked for it (or that was
        over WANTED_SECONDS ago); KeyError: `uid` was removed."""
        from kwil import coursefiles
        from kwil.canvas import CanvasError

        c = self.sync.config["canvas"]
        with self._hold(uid):
            with self.lock:
                table = self._wanted.get(uid or "") or OrderedDict()
                entry = table.pop((course_id, file_id), None)
            if entry is None or self.clock() - entry["at"] > WANTED_SECONDS:
                raise LookupError("not asked for")
            alive = self.alive(uid, digest, entry["generation"])
            store = self.sync.courses
            doc = {"course_id": course_id, "course": entry["course"], "kind": "file",
                   "source_key": f"file:{course_id}:{file_id}", "title": entry["title"],
                   "updated_at": entry["version"], "etag": "", "text": "",
                   "origin": "helper"}
            if data is None:
                code = "too_large"
            elif store.room(uid, course_id) <= 0:
                code = "full"
            else:
                deadline = self.sync.clock() + float(c["content_seconds"])
                try:
                    code = coursefiles.read_file(doc, lambda: data, deadline,
                                                 self.sync.clock)
                except CanvasError:  # out of time: the next manifest asks again
                    code = "timed_out"
                except Exception as e:  # noqa: BLE001 - a file the reader chokes on
                    log.warning("Canvas helper: a pushed file couldn't be read (%s)",
                                type(e).__name__)
                    code = "unreadable"   # kept as a note: not asked for again
            if code in coursefiles.TRANSIENT:
                result = "skipped"   # not kept: the next manifest asks again
            else:
                if code is not None:
                    doc["note"] = code
                result = store.upsert(uid, doc, alive)
        self._dates(uid)
        log.info("Canvas helper: %s pushed file %d of course %d (%d bytes): %s%s", _who(uid),
                 file_id, course_id, len(data or b""), result, f" ({code})" if code else "")
        return {"result": result, "note": code}


def register(app, sync, course_sync, web, error) -> None:
    """Add the routes (student_web.register calls this). With [student]
    or [canvas] content off there is no helper (404); with [canvas] helper
    off its routes are 404 too."""
    from fastapi import Depends, Request
    from fastapi.responses import JSONResponse
    from starlette.concurrency import run_in_threadpool

    from kwil import webapp
    from kwil.public_site import client_key_of
    from kwil.student_web import SyncBusy

    helper = None
    if sync is not None and sync.courses is not None:
        helper = HelperPush(sync, course_sync)
    app.state.canvas_helper = helper  # tests

    def on() -> bool:
        return helper is not None and helper.enabled()

    def nostore(data, status=200):
        return JSONResponse(data, status, headers={"Cache-Control": "no-store"})

    async def wrong(request: Request, what: str):
        """A wrong code or key: counted like a wrong sign-in token (keyed
        as CloudGuard keys its lockout)."""
        ip = client_key_of(request, web)
        if web.limiter.blocked(ip):
            return error("too many attempts; try again later", 429)
        delay = web.limiter.fail(ip)
        log.warning("Canvas helper: a wrong %s", what)
        if delay:
            await webapp._wait(delay)
        return error("not paired; set up the Canvas helper again in Kwil", 401)

    def person(session):
        if not on() or not sync.listed(session.user):
            return False, None
        return True, session.user

    # ---------- from the signed-in app ----------
    @app.post("/api/student/canvas/helper")
    async def helper_setup(session=Depends(webapp.require_session)):
        ok, uid = person(session)
        if not ok:
            return error("not found", 404)
        try:
            code = await run_in_threadpool(helper.keys.start_pairing, uid)
        except OSError:
            return error("couldn't start pairing; try again", 500)
        if code is None:
            return error("not found", 404)
        return nostore({"code": code, "expires_in": PAIR_SECONDS})

    async def revoke(session):
        ok, uid = person(session)
        if not ok:
            return error("not found", 404)
        try:
            had = await run_in_threadpool(helper.keys.revoke, uid)
        except OSError:
            return error("couldn't revoke the helper; try again", 500)
        helper.forget(uid)
        return nostore({"ok": True, "revoked": had})

    @app.post("/api/student/canvas/helper/revoke")
    async def helper_revoke(session=Depends(webapp.require_session)):
        return await revoke(session)

    @app.delete("/api/student/canvas/helper")
    async def helper_revoke_delete(session=Depends(webapp.require_session)):
        return await revoke(session)

    # ---------- from the helper (never a cookie) ----------
    @app.post(PAIR_PATH)
    async def helper_pair(request: Request):
        if not on():
            return error("not found", 404)
        raw = await request.body()
        try:
            body = json.loads(raw) if 0 < len(raw) <= MAX_PAIR_BODY else {}
        except ValueError:
            body = {}
        code = body.get("code") if isinstance(body, dict) else None
        try:
            found = await run_in_threadpool(helper.keys.pair, code)
        except OSError:
            return error("couldn't pair; try again", 500)
        if found is None:
            return await wrong(request, "or expired pairing code")
        _, key = found
        return nostore({"key": key, "manifest": MANIFEST_PATH, "file": FILE_PATH,
                        "max_file_bytes": helper.max_file_bytes(),
                        "max_files": int(sync.config["canvas"]["max_files"])})

    async def pusher(request: Request):
        """(uid, key hash) for the request's sync key, or an error response."""
        auth = request.headers.get("authorization", "")
        key = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
        if not key.startswith(KEY_PREFIX):  # no key at all: nothing guessed
            return error("needs the Canvas helper's key", 401)
        found = await run_in_threadpool(helper.keys.verify, key)
        if found is None:
            return await wrong(request, "sync key")
        uid, digest = found
        if not sync.listed(uid):
            return error("not paired; set up the Canvas helper again in Kwil", 401)
        if not helper.rate.allow(uid or ""):
            response = error("too many pushes; try again later", 429)
            response.headers["Retry-After"] = "600"
            return response
        return uid, digest

    @app.post(MANIFEST_PATH)
    async def helper_manifest(request: Request):
        if not on():
            return error("not found", 404)
        who = await pusher(request)
        if not isinstance(who, tuple):
            return who
        uid, digest = who
        try:
            manifest = clean_manifest(json.loads(await request.body() or b"null"))
        except ValueError as e:
            return error(str(e) if str(e).startswith(("send", "at most", "each", "a listing"))
                         else "bad manifest", 400)
        try:
            return nostore(await run_in_threadpool(helper.manifest, uid, digest, manifest))
        except (TypeError, ValueError) as e:  # a shape clean_manifest let through
            log.warning("Canvas helper: a manifest of %s didn't fit (%s)", _who(uid),
                        type(e).__name__)
            return error("bad manifest", 400)
        except SyncBusy:
            return error("a push is already running", 409)
        except KeyError:
            return error("not found", 404)

    @app.post(FILE_PATH)
    async def helper_file(request: Request):
        if not on():
            return error("not found", 404)
        who = await pusher(request)
        if not isinstance(who, tuple):
            return who
        uid, digest = who
        if not request.headers.get("content-type", "").startswith("multipart/form-data"):
            return error("send the file as multipart/form-data", 400)
        limit = helper.max_file_bytes()
        try:
            form = await request.form(max_files=1, max_fields=4)
        except Exception:  # noqa: BLE001 - any malformed body
            return error("bad request", 400)
        try:
            cid, fid = _id(form.get("course_id")), _id(form.get("file_id"))
            upload = form.get("file")
            if cid is None or fid is None or not hasattr(upload, "read"):
                return error("send course_id, file_id and file", 400)
            data = await upload.read(limit + 1)
        finally:
            await form.close()
        try:
            result = await run_in_threadpool(helper.file, uid, digest, cid, fid,
                                             data if len(data) <= limit else None)
        except SyncBusy:
            return error("a push is already running", 409)
        except LookupError as e:
            if isinstance(e, KeyError):
                return error("not found", 404)
            return error("not asked for; push the manifest first", 409)
        finally:
            del data
        return nostore(result)
