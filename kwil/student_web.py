"""The phone app's school deadlines: connect Canvas, sync, and the "What I
captured" list (web/student.js), for the owner and every invited person,
each on their own deadlines (deadlines.py).

  GET  /api/student/items              {items, canvas: status} (the person's own)
  POST /api/student/canvas             {ics_url} or {base_url, token}: save their
                                       Canvas link (409 for the owner while
                                       CANVAS_ICS_URL / CANVAS_TOKEN is set)
  POST /api/student/disconnect         {delete_items?}: forget the link and its
                                       course content (and, asked, the deadlines
                                       that came from Canvas); while the env
                                       is set, only an app-stored owner's link (else 409)
  POST /api/student/sync               fetch now: at most every [student]
                                       sync_minutes per person, one at a time;
                                       course content follows in the background
  POST /api/student/items/{id}/resolve {choice: "confirm" | "dismiss" | a date}
  DELETE /api/student/items/{id}       delete one (also POST .../{id}/delete)
  DELETE /api/student/source/{source}  delete all from one source (also POST
                                       .../{source}/delete: the failover
                                       preflight allows only GET and POST)
  GET  /api/student/schools?q=         Canvas's school lookup (canvas.find_school),
                                       each school with its `tokens` policy
  GET  /api/student/canvas/policy?host= {host, tokens}: whether that Canvas lets
                                       students make an access token ("allowed",
                                       "blocked", "unknown"; canvas_schools.py)
  POST /api/student/canvas/report      {host, tokens: "blocked"}: "my Canvas has
                                       no New access token button" (REPORTS an
                                       hour per person)
  GET  /api/student/courses            what course content is stored (coursefiles
                                       stats: courses, documents, bytes; no text)
  POST /api/student/courses/{id}/delete  delete one course's content (also DELETE
                                       /api/student/courses/{id})

All need a signed-in session (webapp.require_session); each person only
ever reaches their own file, so there is no id of a person in any path.
With [student] off they are 404.

An invited person's link is stored with userkeys.seal under
canvas.CANVAS_LABEL, bound to their id, in `<[family] dir>/<id>/canvas.enc`
(google_auth.write_private), deleted with their folder. The owner's is
bound to "owner" (as push.py's; invited ids are u_..., so they can't
collide) in `<[student] dir>/canvas.enc`, unless the server's environment
has CANVAS_ICS_URL or CANVAS_TOKEN: then the environment wins ("managed":
"env"): the app doesn't store the owner's link (409), and Disconnect
only deletes an older app-stored file (409 if there is none).
Without a usable USER_DATA_KEY nothing is stored (503). The feed URL is
itself a secret (it reads their calendar without a sign-in) and is treated like the token:
never logged, never returned (the status says only which kind and the
school's host). A school password is never asked for or stored.

The way in is picked for the person (web/student.js), from the school's
token policy (canvas_schools.Policies: a sourced seed plus what this
server learned: a token sync that worked records "allowed", a report or
the Canvas helper's reading "blocked"); nobody chooses between feed,
token and helper. `[canvas] tokens_for = "owner"` keeps the token way for
the owner only: invited people get the helper or the feed (the status
says `tokens_offered`), and connecting with a token answers them 403
{code: "tokens_owner_only"}.

Sync (StudentSync.sync, also callable by a scheduler): the calendar feed
(source "ics") or the planner over the API ("canvas_api"; module pages
too with [canvas] pages, "modules"), then, with [student] gmail and their
Gmail connected, Canvas's notification emails from the last 14 days
("gmail", read-only, each email read once). Claude reads only emails,
pages and course content (canvas.extract_from_pages), on the person's own
Claude key when they have one (family.UserApi), with [student]
extract_model. Logs: ids, counts, source names and error codes only.

Course content ([canvas] content, an access token only): the syllabus,
pages, assignment descriptions, announcements, module lists and files of
each active course, as text in the person's own coursefiles.sqlite
(coursefiles.sync_content), within [canvas] content_seconds of its own so
it never starves the planner; then dates in it go to the deadline list as
drafts (source "course_text", read once per text version). It runs in the
background (CourseSync): every [canvas] auto_sync_hours for everyone
connected (the owner's only where the failover leader check says this
machine leads), and right after "Sync now" (which the page also runs as
soon as Canvas is connected), never in the request; "Sync now" while the
background holds the person's sync answers {content: "queued",
background: true}. Disconnect deletes the person's whole content store,
and a sync that started before it (the generation is read before the
link) writes nothing more, nor once the link is gone.

The Canvas helper (canvas_helper.py, its routes added by register()):
for schools that block access tokens, a browser extension in the
person's own Canvas tab pushes the same content with a sync key; the
background reads the dates in it (CourseSync.request_dates). Disconnect
revokes that key too; the status says only whether one is paired
("helper": {enabled, paired, pairing, paired_at}).
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

from kwil import canvas
from kwil.deadlines import SOURCES, DeadlineError

log = logging.getLogger(__name__)

CANVAS_FILE = "canvas.enc"      # in <[family] dir>/<id>/, the owner's in [student] dir
OWNER_UID = "owner"             # the owner's id inside the sealed record (push.py's too)
OWNER_ICS_ENV = "CANVAS_ICS_URL"
OWNER_TOKEN_ENV = "CANVAS_TOKEN"
MAX_TOKEN = 200
_TOKEN = re.compile(r"^[A-Za-z0-9~_.-]{20,200}$")
MAX_BODY = 8192                 # bytes of a request body
SCHOOL_LOOKUPS = 10             # per person per minute
REPORTS = 3                     # token-policy reports per person per hour
MAX_PEOPLE = 1024               # entries in the throttle and lock maps
CANVAS_SOURCES = ("ics", "canvas_api", "modules", "course_text")
TICK_SECONDS = 300              # the background sync looks for who is due this often
FIRST_TICK = 60                 # ... first this long after startup
RETRY_MINUTES = 30              # a run that left files for later, or failed: again after this
MAX_PER_TICK = 20               # people synced in one tick, at most (the rest next tick)
BUSY_SECONDS = 30               # an asked-for sync found another running: look again after this


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
        # Disconnect bumps a person's generation: a content sync that started
        # before writes nothing more (coursefiles.CourseStore.upsert's alive)
        self._generation: OrderedDict[str, int] = OrderedDict()
        self._generations = 0
        # coursefiles.CourseStore with [canvas] content: Family's (it guards
        # members' writes), else the process's
        self.courses = None
        if config.get("canvas", {}).get("content"):
            from kwil import coursefiles

            self.courses = getattr(family, "coursefiles", None) or \
                coursefiles.open_store(config)
        # the Canvas helper's sync keys (canvas_helper.py): Disconnect revokes them
        from kwil.canvas_helper import HelperKeys

        self.helper = HelperKeys(config, family)
        self._reports: OrderedDict[str, deque] = OrderedDict()
        # links connected in the app whose first working sync is still to come:
        # only that one counts as "allowed" for the school (a token re-used for
        # months, a TA's or one made before a restriction says nothing new)
        self._fresh: OrderedDict[str, bool] = OrderedDict()
        # (person, host) reports already counted: each person's claim once
        self._reported: OrderedDict[tuple, bool] = OrderedDict()
        # the schools' token policies (seed + learned), for picking the way in
        from kwil.canvas_schools import Policies

        self.policies = Policies(config["student"]["dir"])

    @property
    def env(self):
        return os.environ if self.environ is None else self.environ

    # ---------- whose ----------
    def listed(self, uid) -> bool:
        return uid is None or (self.family is not None and self.family.get(uid) is not None)

    def timezone(self, uid):
        from kwil.tools.timeutil import local_zone

        name = ""
        if uid is not None and self.family is not None:
            name = (self.family.get(uid) or {}).get("timezone") or ""
        try:
            return local_zone(name or self.config["assistant"]["timezone"] or None)
        except Exception:  # a zone this machine doesn't know
            return local_zone(None)

    def _file(self, uid) -> Path:
        if uid is None:
            return Path(self.config["student"]["dir"]) / CANVAS_FILE
        return Path(self.config["family"]["dir"]) / uid / CANVAS_FILE

    def env_managed(self) -> bool:
        """The owner's Canvas comes from CANVAS_ICS_URL / CANVAS_TOKEN (read
        on every call: deleting them hands the link to the app at once)."""
        return bool(self.env.get(OWNER_ICS_ENV, "").strip()
                    or self.env.get(OWNER_TOKEN_ENV, "").strip())

    def _keys_env(self):
        return self.family.keys.environ if self.family is not None else self.environ

    def tokens_offered(self, uid) -> bool:
        """May this person connect with an access token? The owner always;
        an invited person unless [canvas] tokens_for is "owner" (anything
        but "everyone" counts as "owner": the stricter one)."""
        if uid is None:
            return True
        return str(self.config["canvas"].get("tokens_for", "everyone")).strip().lower() \
            == "everyone"

    # ---------- their Canvas link ----------
    def credentials(self, uid) -> dict | None:
        """{kind: "ics", ics_url} or {kind: "api", base_url, token}, or None."""
        if uid is None and self.env_managed():
            ics = self.env.get(OWNER_ICS_ENV, "").strip()
            token = self.env.get(OWNER_TOKEN_ENV, "").strip()
            base = self.config["canvas"]["base_url"].strip()
            if token and base:
                return {"kind": "api", "base_url": base, "token": token}
            if ics:
                return {"kind": "ics", "ics_url": ics}
            return None
        from kwil.userkeys import unseal

        try:
            raw = self._file(uid).read_bytes()
        except FileNotFoundError:
            return None
        except OSError as e:
            log.warning("Student: %s's Canvas file is unreadable (%s)", uid or "the owner",
                        type(e).__name__)
            return None
        data = unseal(canvas.CANVAS_LABEL, uid or OWNER_UID, raw, self._keys_env(),
                      what="Canvas link", redo="connect Canvas again")
        if data is None or data.get("kind") not in ("ics", "api"):
            return None
        return data

    def status(self, uid) -> dict:
        """What the card shows; never the feed URL or the token."""
        from kwil import userkeys

        found = self.credentials(uid)
        school = None
        if found is not None:
            school = urlsplit(found.get("base_url") or found.get("ics_url") or "").hostname
        env = uid is None and self.env_managed()
        helper = self.helper.status(uid)
        # their school's token policy: the link's host, else the one the helper read
        known = school or helper.get("canvas_host")
        return {"connected": found is not None,
                "kind": found["kind"] if found else None, "school": school,
                "managed": "env" if env else "app",
                # env-managed, with an older link still stored by the app: only
                # that file can be forgotten here (Disconnect), never the env
                "app_link": env and self._file(None).exists(),
                "can_store": env or userkeys.secret_problem(self._keys_env()) is None,
                "gmail": bool(self.config["student"]["gmail"]) and self._inbox(uid) is not None,
                "last_sync": self.store.last_sync(uid),
                "tokens_offered": self.tokens_offered(uid),
                "policy": {"host": known, "tokens": self.policies.policy(known, own=True)}
                if known else None,
                # the browser helper (canvas_helper.py): never the key or a code
                "helper": {"enabled": self.helper_on(), **helper}}

    def helper_on(self) -> bool:
        return self.courses is not None and bool(self.config["canvas"].get("helper"))

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

    def connect(self, uid, record: dict) -> bool:
        """Seal and store a person's link (replacing any); `uid` None is
        the owner's. False if they were removed meanwhile;
        userkeys.KeyProblem 503 without USER_DATA_KEY (nothing written)."""
        from kwil.tools.google_auth import write_private
        from kwil.userkeys import seal

        text = seal(canvas.CANVAS_LABEL, uid or OWNER_UID, record, self._keys_env())

        def write():
            path = self._file(uid)
            path.parent.mkdir(parents=True, exist_ok=True)
            write_private(path, text)

        if uid is None:
            write()
        else:
            with self.family.while_listed(uid) as listed:
                if not listed:
                    return False
                write()
        with self.lock:  # a new link may sync at once
            self._last.pop(uid or "", None)
            if record["kind"] == "api":
                self._fresh[uid or ""] = True
                self._fresh.move_to_end(uid or "")
                _trim(self._fresh)
            else:
                self._fresh.pop(uid or "", None)
        log.info("Student: %s connected Canvas (%s)", uid or "the owner", record["kind"])
        return True

    def disconnect(self, uid, delete_items: bool = False) -> dict:
        from kwil.family import _unlink

        path = self._file(uid)
        had = path.exists()
        _unlink(path)
        helper = self.helper.revoke(uid)  # its pushes stop too
        with self.lock:  # a content sync (or a helper's push) running now stops writing
            self._fresh.pop(uid or "", None)
            self._generations += 1
            self._generation[uid or ""] = self._generations
            self._generation.move_to_end(uid or "")
            _trim(self._generation)
        # the course content always goes: it was a copy read with that link
        documents = self.courses.delete_all(uid) if self.courses is not None else 0
        deleted = 0
        if delete_items:
            for source in CANVAS_SOURCES:
                deleted += self.store.delete_source(uid, source)
        log.info("Student: %s disconnected Canvas (%d item(s), %d document(s) deleted)",
                 uid or "the owner", deleted, documents)
        return {"connected": False, "had": had or helper, "deleted": deleted}

    # ---------- syncing ----------
    def _inbox(self, uid):
        if uid is None:
            from kwil.cli import owner_gmail

            return owner_gmail(self.config, output_fn=lambda *_: None)
        user = self.family.get(uid) if self.family is not None else None
        return self.family.gmail_inbox(user) if user is not None else None

    def _claude(self, uid):
        """(client, meter) for the extraction call: an invited person's
        own key (or, family tier, the owner's), else the owner's."""
        if uid is not None:
            from kwil.family import UserApi

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
        # (key, text[, sent_at]); at most one call's worth: the rest next sync
        fresh = [x for x in texts if x[0] in keys][:canvas.MAX_TEXTS]
        if not fresh:
            return []
        client, meter = self._claude(uid)
        found = canvas.extract_from_pages(client, self._model(), fresh, tz=tz, meter=meter)
        self.store.mark_seen(uid, source, [x[0] for x in fresh])
        return found

    def content_on(self, found) -> bool:
        """Course content is read for this link (an access token)."""
        return self.courses is not None and found is not None and found["kind"] == "api"

    def _generation_of(self, uid):
        with self.lock:
            return self._generation.get(uid or "")

    def _alive(self, uid, generation):
        """alive() for a sync that started at `generation`: true while
        nobody disconnected since and the link is still there (the app's
        file, or the owner's environment)."""
        key = uid or ""

        def alive() -> bool:
            with self.lock:
                if self._generation.get(key) != generation:
                    return False
            return self._file(uid).exists() or (uid is None and self.env_managed())

        return alive

    def _content(self, uid, found, now, generation) -> dict:
        """The course content step (coursefiles.sync_content), on its own
        [canvas] content_seconds; it writes nothing once they disconnect
        after the sync started (`generation`, read before the link was)."""
        from kwil import coursefiles

        c = self.config["canvas"]
        alive = self._alive(uid, generation)
        if not alive():
            return {}

        base = canvas.base_url(found["base_url"])  # checked again: DNS can change
        api = canvas.CanvasAPI(base, found["token"], timeout=c["timeout"], clock=self.clock,
                               deadline=self.clock() + float(c["content_seconds"]))
        try:
            return coursefiles.sync_content(
                api, self.courses, uid, max_files=int(c["max_files"]),
                max_file_bytes=int(float(c["max_file_mb"]) * 1_000_000), alive=alive,
                now=now)
        finally:
            api.close()

    def sync(self, uid, content: bool = False) -> dict:
        """Read every source the person has; {sources: {source: counts},
        errors: {source: code}}, and with `content` (and an access token)
        {content: counts} too, the course content step last. Raises
        KeyError if they were removed."""
        from datetime import datetime, timezone

        tz, now = self.timezone(uid), datetime.now(timezone.utc)
        # the whole sync's time (each request's timeout is per read only)
        deadline = self.clock() + canvas.SYNC_SECONDS
        out, errors = {}, {}
        # before the link is read: a Disconnect from here on stops the content writes
        generation = self._generation_of(uid)
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
                if "canvas_api" in out and self._first_sync(uid):
                    # a token just made in the app works: the school allows them
                    self.policies.record(found["base_url"], "allowed")
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
        result = {"sources": out, "errors": errors}
        if content and self.content_on(found):
            result["content"] = self._content_step(uid, found, tz, now, run, errors,
                                                   generation)
        self.store.note_sync(uid)
        log.info("Student: synced %s (%d source(s), %d failed)", uid or "the owner",
                 len(out), len(errors))
        return result

    def _content_step(self, uid, found, tz, now, run, errors, generation) -> dict:
        """The content, then the dates in it (drafts, each text version
        read once), even when the content step stopped partway."""
        counts = {}
        try:
            counts = self._content(uid, found, now, generation)
        except KeyError:
            raise
        except canvas.CanvasError as e:
            errors["content"] = str(e)
        except Exception as e:  # never the message: it can hold a URL or text
            errors["content"] = _code(e)
        if "content" in errors:
            log.warning("Student: %s's content sync failed (%s)", uid or "the owner",
                        errors["content"])
        run("course_text", lambda: canvas.upcoming(self._extract(
            uid, "course_text", self.courses.dated_texts(uid), tz), now))
        return counts

    def try_sync(self, uid, content: bool = False, throttle: bool = True,
                 record: bool = True) -> dict:
        """sync(), unless one is running for them (SyncBusy(0)) or, with
        `throttle`, the last recorded one started less than [student]
        sync_minutes ago (SyncBusy(wait)). `record`: this one counts for
        that throttle ("Sync now"; the background's runs don't, so a tap
        right after one of them isn't refused)."""
        key, gap = uid or "", float(self.config["student"]["sync_minutes"]) * 60
        with self.lock:
            running = self._running.get(key)
            if running is None:
                running = self._running[key] = threading.Lock()
            self._running.move_to_end(key)
            if running.locked():
                raise SyncBusy(0)
            last = self._last.get(key)
            if throttle and last is not None and self.clock() - last < gap:
                raise SyncBusy(gap - (self.clock() - last))
            if not running.acquire(blocking=False):
                raise SyncBusy(0)
            if record:
                self._last[key] = self.clock()
                self._last.move_to_end(key)
                _trim(self._last)
            idle = [k for k, lock in self._running.items() if not lock.locked()]
            for k in idle[:max(0, len(self._running) - MAX_PEOPLE)]:
                del self._running[k]
        try:
            return self.sync(uid, content=content)
        finally:
            running.release()

    def course_dates(self, uid) -> dict:
        """The dates in the person's course content, as the content step
        reads them (drafts, source "course_text", each text version read
        once), for what the Canvas helper pushed. SyncBusy(0) while a sync
        of theirs runs."""
        from datetime import datetime, timezone

        key = uid or ""
        with self.lock:
            running = self._running.get(key)
            if running is None:
                running = self._running[key] = threading.Lock()
            self._running.move_to_end(key)
            if not running.acquire(blocking=False):
                raise SyncBusy(0)
        try:
            tz, now = self.timezone(uid), datetime.now(timezone.utc)
            return self.store.propose(uid, canvas.upcoming(self._extract(
                uid, "course_text", self.courses.dated_texts(uid), tz), now),
                "course_text", tz=tz)
        finally:
            running.release()

    def own_hosts(self, uid) -> set:
        """The person's own Canvas hosts: their link's, and the one their
        helper reads."""
        from kwil.canvas_schools import normalize

        found = self.credentials(uid) or {}
        hosts = {normalize(found.get("base_url") or found.get("ics_url") or ""),
                 self.helper.status(uid).get("canvas_host")}
        return {h for h in hosts if h}

    def policy_for(self, uid, host) -> str:
        """A school's token policy as this person may see it: what others'
        events taught only once there are canvas_schools.MIN_EVENTS of them,
        unless it is their own Canvas host."""
        from kwil.canvas_schools import normalize

        name = normalize(host)
        return self.policies.policy(name, own=name is not None and name in self.own_hosts(uid))

    def report_blocked(self, uid, host: str) -> bool:
        """Count a person's "no New access token button" for a host, once
        per person and host (in memory, bounded); False if already counted."""
        key = (uid or "", host)
        with self.lock:
            if key in self._reported:
                return False
            self._reported[key] = True
            while len(self._reported) > MAX_PEOPLE:
                self._reported.popitem(last=False)
        return self.policies.record(host, "blocked")

    def _first_sync(self, uid) -> bool:
        """True once per link connected in the app: its first working sync."""
        with self.lock:
            return self._fresh.pop(uid or "", False)

    def lookup_allowed(self, uid) -> bool:
        """At most SCHOOL_LOOKUPS school searches (or policy look-ups) a
        minute per person."""
        return self._allowed(self._lookups, uid, SCHOOL_LOOKUPS, 60)

    def report_allowed(self, uid) -> bool:
        """At most REPORTS token-policy reports an hour per person."""
        return self._allowed(self._reports, uid, REPORTS, 3600)

    def _allowed(self, table: OrderedDict, uid, limit: int, window: float) -> bool:
        key, now = uid or "", self.clock()
        with self.lock:
            recent = table.setdefault(key, deque())
            table.move_to_end(key)
            while recent and now - recent[0] > window:
                recent.popleft()
            if len(recent) >= limit:
                return False
            recent.append(now)
            _trim(table)
            return True


class CourseSync:
    """The background sync, so nobody has to tap anything once Canvas is
    connected: every TICK_SECONDS it syncs (StudentSync.try_sync, course
    content included) whoever is due: connected, and last synced [canvas]
    auto_sync_hours ago (or never), or asked for by request() ("Sync now",
    a new link). At most MAX_PER_TICK people a tick, one after another.
    The owner's runs only where `leader` (a failover LeaderCheck; None:
    no peers) says this machine leads, so his Canvas and Claude are read on
    one machine. Not made on a side-port check copy ([candidate])."""

    def __init__(self, sync: StudentSync, config: dict, family=None, leader=None,
                 clock=time.time):
        self.sync = sync
        self.hours = float(config["canvas"]["auto_sync_hours"])
        self.family = family
        self.leader = leader
        self.clock = clock
        self.lock = threading.Lock()
        self._asked: OrderedDict[str, bool] = OrderedDict()
        self._retry: OrderedDict[str, float] = OrderedDict()   # key -> clock() when due
        self._dates: OrderedDict[str, bool] = OrderedDict()    # helper pushes to read dates from
        self._running: set[str] = set()   # whose sync this thread runs right now
        self._busy = False                 # an asked-for sync waits for another one
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None

    def running(self, uid) -> bool:
        """Is this thread syncing this person right now?"""
        with self.lock:
            return (uid or "") in self._running

    def people(self) -> list:
        """The owner (None) and every active invited person's id."""
        return people(self.family)

    def request_dates(self, uid) -> None:
        """Read the dates in what the Canvas helper pushed at the next tick
        (not at once: the files a manifest asked for arrive right after
        it). Where the push landed, the owner's too: a push reaches one
        machine, so no leader check is needed."""
        with self.lock:
            self._dates[uid or ""] = True
            self._dates.move_to_end(uid or "")
            _trim(self._dates)

    def request(self, uid, wake: bool = True) -> None:
        """Sync this person soon (in the background thread; `wake`: now)."""
        with self.lock:
            self._asked[uid or ""] = True
            self._asked.move_to_end(uid or "")
            _trim(self._asked)
        if wake:
            self._wake.set()

    def _last(self, uid) -> float | None:
        from datetime import datetime

        value = self.sync.store.last_sync(uid)
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() \
                if value else None
        except ValueError:
            return None

    def due(self, uid) -> bool:
        key = uid or ""
        with self.lock:
            if key in self._asked:
                return True
            retry = self._retry.get(key)
        if retry is not None:
            return self.clock() >= retry
        if self.hours <= 0:
            return False
        last = self._last(uid)
        return last is None or self.clock() - last >= self.hours * 3600

    def _leads(self) -> bool:
        try:
            return self.leader is None or self.leader.is_leader()
        except Exception as e:  # noqa: BLE001 - a probe failure: try next tick
            log.warning("Student: leader check failed (%s)", type(e).__name__)
            return False

    def tick(self) -> int:
        """Sync whoever is due; returns how many syncs ran."""
        ran = 0
        self._busy = False
        for uid in self.people():
            if self._stop.is_set() or ran >= MAX_PER_TICK:
                break
            if not self.due(uid):
                continue
            key = uid or ""
            with self.lock:
                asked = self._asked.pop(key, False)
            if self.sync.credentials(uid) is None:
                continue
            if uid is None and not self._leads():
                continue
            with self.lock:
                self._running.add(key)
            try:
                # asked: "Sync now" just ran without the content; go again at once
                result = self.sync.try_sync(uid, content=True, throttle=not asked,
                                            record=False)
            except SyncBusy:
                if asked:  # kept for the next look, BUSY_SECONDS on: never a spin
                    self.request(uid, wake=False)
                    self._busy = True
                continue
            except KeyError:  # removed meanwhile
                continue
            except Exception as e:  # noqa: BLE001 - never the message: it can hold text
                log.warning("Student: background sync for %s failed (%s)", uid or "the owner",
                            type(e).__name__)
                result = {}
            finally:
                with self.lock:
                    self._running.discard(key)
            ran += 1
            # files left for later, or out of time: again sooner than auto_sync_hours
            # (never after a rate limit or a failure that would only fail again)
            more = (result.get("content") or {}).get("more") \
                or (result.get("errors") or {}).get("content") == "timed_out"
            with self.lock:
                if more:
                    self._retry[key] = self.clock() + RETRY_MINUTES * 60
                    self._retry.move_to_end(key)
                    _trim(self._retry)
                else:
                    self._retry.pop(key, None)
        if ran:
            log.info("Student: background sync ran for %d person(s)", ran)
        self._read_dates()
        return ran

    def _read_dates(self) -> None:
        """The dates in what the Canvas helper pushed (request_dates)."""
        with self.lock:
            keys = list(self._dates)[:MAX_PER_TICK]
        for key in keys:
            if self._stop.is_set():
                break
            with self.lock:
                self._dates.pop(key, None)
            uid = key or None
            try:
                if self.sync.listed(uid):
                    self.sync.course_dates(uid)
            except SyncBusy:  # a sync of theirs runs: next tick
                self.request_dates(uid)
            except KeyError:  # removed meanwhile
                continue
            except Exception as e:  # noqa: BLE001 - never the message: it can hold text
                log.warning("Student: reading dates in %s's pushed content failed (%s)",
                            uid or "the owner", type(e).__name__)

    def _loop(self) -> None:
        delay = FIRST_TICK
        while not self._stop.is_set():
            self._wake.wait(delay)
            self._wake.clear()
            if self._stop.is_set():
                break
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001 - keep the thread alive
                log.error("Student: background sync check failed (%s)", type(e).__name__)
            delay = BUSY_SECONDS if self._busy else TICK_SECONDS

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="course-sync", daemon=True)
        self._thread.start()
        log.info("Student: background Canvas sync every %g hour(s)", self.hours)

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()


def leader_check(config: dict):
    """A failover.leader.LeaderCheck for this machine's role among its
    peers, or None (no failover, or no peers: this machine leads)."""
    try:
        from kwil.failover.peers import settings_from_config

        failover = settings_from_config(config)
    except Exception:  # noqa: BLE001 - a bad [failover] section is reported elsewhere
        return None
    peers = [b for b in failover.backends if b.role != failover.role]
    if not failover.role or not peers:
        return None
    from kwil.failover.leader import LeaderCheck

    return LeaderCheck(failover.role, peers)


def people(family) -> list:
    """The owner (None) and every active invited person's id."""
    out = [None]
    if family is not None:
        with family.lock:
            users = list(family.users.values())
        out += [u["id"] for u in users if u.get("status") == "active" and u.get("id")]
    return out


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

    from kwil import canvas_schools, deadlines, userkeys, webapp

    store = family.deadlines if family is not None else deadlines.current()
    config = family.config if family is not None else (store.config if store else None)
    if store is None or config is None or not config.get("student", {}).get("enabled"):
        sync = None
    else:
        sync = StudentSync(store, config, family)
    app.state.student = sync  # tests, and a scheduler
    # the background sync (webapp's lifespan starts it); never on a check copy
    courses = None
    if sync is not None and not config.get("candidate"):
        courses = CourseSync(sync, config, family, leader=leader_check(config))
    app.state.course_sync = courses

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
        if uid is None and sync.env_managed():
            return error(f"your server's environment sets {OWNER_ICS_ENV} / "
                         f"{OWNER_TOKEN_ENV}, and it wins; remove them there to connect "
                         "Canvas here", 409)
        if userkeys.secret_problem(sync._keys_env()) is not None:  # fail closed
            log.warning("Student: can't store %s's Canvas link (%s isn't usable)",
                        uid or "the owner", userkeys.ENV)
            return error("this server can't store sign-ins yet (its owner needs "
                         f"to set {userkeys.ENV})", 503)
        body = await body_of(request)
        if not body.get("ics_url") and (body.get("token") or body.get("base_url")) \
                and not sync.tokens_offered(uid):  # [canvas] tokens_for = "owner"
            return nostore({"error": "on this Kwil, access tokens are for its owner only; "
                            "use the calendar feed or the Canvas helper",
                            "code": "tokens_owner_only"}, 403)
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
        # no background request here: the page syncs right after connecting,
        # and that sync queues the content (a request now would race it)
        return nostore(await run_in_threadpool(sync.status, uid))

    @app.post("/api/student/disconnect")
    async def student_disconnect(request: Request, session=Depends(webapp.require_session)):
        ok, uid = person(session)
        if not ok:
            return error("not found", 404)
        if uid is None and sync.env_managed() and not sync._file(None).exists() \
                and not sync.helper.status(None)["paired"]:
            # nothing of the app's to forget: the env stays (only its owner edits it)
            return error(f"on your own server, remove {OWNER_ICS_ENV} / {OWNER_TOKEN_ENV} "
                         "from its environment", 409)
        body = await body_of(request)
        try:
            result = await run_in_threadpool(sync.disconnect, uid,
                                             bool(body.get("delete_items")))
        except (OSError, KeyError):
            return error("couldn't disconnect Canvas; try again", 500)
        if getattr(app.state, "canvas_helper", None) is not None:
            app.state.canvas_helper.forget(uid)
        return nostore(result)

    @app.post("/api/student/sync")
    async def student_sync(session=Depends(webapp.require_session)):
        ok, uid = person(session)
        if not ok:
            return error("not found", 404)
        try:
            result = await run_in_threadpool(sync.try_sync, uid)
        except SyncBusy as e:
            if not e.wait and courses is not None and courses.running(uid):
                # the background sync has it (content included): not an error
                return nostore({"sources": {}, "errors": {}, "content": "queued",
                                "background": True})
            wait = int(e.wait) + 1 if e.wait else 0
            response = error("a sync is already running" if not wait else
                             f"synced recently; try again in {wait // 60 + 1} minute(s)",
                             409 if not wait else 429)
            if wait:
                response.headers["Retry-After"] = str(wait)
            return response
        except KeyError:
            return error("not found", 404)
        if courses is not None and await run_in_threadpool(
                lambda: sync.content_on(sync.credentials(uid))):
            courses.request(uid)  # the course content follows in the background
            result["content"] = "queued"
        return nostore(result)

    def parse_id(item_id: str):
        return int(item_id) if re.fullmatch(r"\d{1,12}", item_id or "") else None

    @app.get("/api/student/courses")
    async def student_courses(session=Depends(webapp.require_session)):
        ok, uid = person(session)
        if not ok:
            return error("not found", 404)
        if sync.courses is None:
            return nostore({"enabled": False, "courses": [], "documents": 0, "bytes": 0,
                            "last_sync": None})
        stats = await run_in_threadpool(sync.courses.stats, uid)
        return nostore({"enabled": True, **stats})

    async def delete_course(course_id: str, session):
        ok, uid = person(session)
        cid = parse_id(course_id)
        if not ok or cid is None or sync.courses is None:
            return error("not found", 404)
        try:
            gone = await run_in_threadpool(sync.courses.delete_course, uid, cid)
        except KeyError:
            return error("not found", 404)
        return {"ok": True, "deleted": gone}

    @app.delete("/api/student/courses/{course_id}")
    async def student_delete_course(course_id: str, session=Depends(webapp.require_session)):
        return await delete_course(course_id, session)

    @app.post("/api/student/courses/{course_id}/delete")
    async def student_delete_course_post(course_id: str,
                                         session=Depends(webapp.require_session)):
        return await delete_course(course_id, session)

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

        def find():
            return [{**s, "tokens": sync.policy_for(uid, s.get("base_url"))}
                    for s in canvas.find_school(q)]
        return nostore({"schools": await run_in_threadpool(find)})

    @app.get("/api/student/canvas/policy")
    async def student_policy(host: str = "", session=Depends(webapp.require_session)):
        ok, uid = person(session)
        if not ok:
            return error("not found", 404)
        name = canvas_schools.normalize(host)
        if name is None:
            return error("send a Canvas host, e.g. canvas.school.edu", 400)
        if not sync.lookup_allowed(uid):
            return error("too many searches; wait a minute", 429)
        return nostore({"host": name,
                        "tokens": await run_in_threadpool(sync.policy_for, uid, name)})

    @app.post("/api/student/canvas/report")
    async def student_report(request: Request, session=Depends(webapp.require_session)):
        """A person's own word: their Canvas shows no New access token
        button. A claim: counted, and never over a seed checked later."""
        ok, uid = person(session)
        if not ok:
            return error("not found", 404)
        body = await body_of(request)
        name = canvas_schools.normalize(body.get("host"))
        if name is None or body.get("tokens") != "blocked":
            return error('send {host, tokens: "blocked"}', 400)
        if not sync.report_allowed(uid):
            return error("too many reports; try again later", 429)
        counted = await run_in_threadpool(sync.report_blocked, uid, name)
        log.info("Student: %s reported their school blocks access tokens (%s)",
                 uid or "the owner", "counted" if counted else "already counted")
        return nostore({"host": name,
                        "tokens": await run_in_threadpool(sync.policy_for, uid, name)})

    # the Canvas helper's pairing and push routes (canvas_helper.py)
    from kwil import canvas_helper

    canvas_helper.register(app, sync, courses, web, error)
