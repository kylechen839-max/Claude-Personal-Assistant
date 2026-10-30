"""The Today screen (web/today.js): one read-only look at a person's day.

  GET /api/today   {"date", "tomorrow", "brief", "calendar", "deadlines",
                    "notion", "reminders", "research", "study"}

Each section is {"state": "ok" | "off" | "reconnect" | "error", "items"}
with at most MAX_ROWS rows (the calendar MAX_ROWS a day; "more": true when
rows were cut), and one failing section never hides the others. Times and
dates are local ISO ("2027-01-14T15:00", "2027-01-15"); the page words
them in its own language. One screen in flight per person (Gate), and
the calendar and Notion reads are bounded per person and in all (Reads). Everything is the signed-in person's own (the owner's, or an
invited person's: their own calendar tool, deadline store, reminders,
research jobs and study list); someone no longer listed gets a 404, and
the answer is never cached (no-store).

Nothing here writes or asks Claude: the calendar is read by calling the
`list_calendar_events` handler itself (primary calendar, today and
tomorrow; no dispatch, so no "tool" event and no taint on the open
conversation), Notion by `list_assignments` (days_ahead=1). "Today" and
"tomorrow" come from one reading of the clock (chat._now in the person's
zone), set as timeutil.TURN_NOW while the sections are read, so a request
at 23:59:59 can't put Monday's events under Tuesday.

`outside` marks a row whose title someone else could have written (an
invited event, every deadline and Notion assignment, a reminder or study
item made while outside text was in the conversation): the page never
puts such a title into the "Ask Kwil" question it prefills. The log gets
section names, counts, states and exception type names only.
"""

import asyncio
import contextvars
import logging
import re
import threading
import time as time_mod
from collections import OrderedDict, deque
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import date, datetime, time, timedelta

from kwil.tools import UNTRUSTED_MARK

log = logging.getLogger(__name__)

MAX_ROWS = 10        # per section
MAX_RESEARCH = 3
MAX_STUDY = 5
OVERDUE_DAYS = 14    # older overdue deadlines stay in Settings, not on Today
SLOW_SECONDS = 8.0   # calendar and Notion together: past this, "error"
PER_MINUTE = 20      # screens a person may load a minute (joining one in flight is free)
MAX_LIVE = 16        # calendar/Notion reads alive at once, everyone together
MAX_PEOPLE = 1000    # rate-limit entries kept
MAX_WAITERS = 4      # requests a person may have waiting on their screen in flight
RUNNING = ("queued", "planning", "researching", "writing")
# exception names that mean "sign in again" (as student_web._code)
RECONNECT = ("MissingScopes", "GoogleSignInError", "RefreshError", "NotionReconnect")



class Reads:
    """The calendar and Notion reads, each on a thread of its own, never in
    a queue: a person has at most one read of each alive (a new screen
    while their last one still hangs gets "error" for it, no second call),
    and at most MAX_LIVE are alive for everyone together. So one person's
    hung Notion can't hold up anyone else's calendar, and nothing waits to
    start after its screen gave up. A hung read keeps its thread until
    Google or Notion answers (Python can't stop a thread)."""

    def __init__(self, limit: int = MAX_LIVE):
        self.limit = limit
        self.lock = threading.Lock()
        self.live: dict = {}  # (person, section) -> Future

    def start(self, key: tuple, fn, *args) -> Future | None:
        """A Future for fn(*args) run in a copy of this context (TURN_NOW,
        the language), or None: this person's last one is still running,
        or MAX_LIVE are."""
        with self.lock:
            last = self.live.get(key)
            if last is not None and not last.done():
                return None
            if len(self.live) >= self.limit:
                return None
            future: Future = Future()
            self.live[key] = future
        ctx = contextvars.copy_context()

        def run():
            try:
                future.set_result(ctx.run(fn, *args))
            except BaseException as e:  # noqa: BLE001 - handed to the screen
                future.set_exception(e)
            finally:
                with self.lock:
                    if self.live.get(key) is future:
                        del self.live[key]
        future.set_running_or_notify_cancel()
        try:
            threading.Thread(target=run, name=f"today-{key[1]}", daemon=True).start()
        except BaseException as e:  # e.g. "can't start new thread": no entry left behind
            with self.lock:
                if self.live.get(key) is future:
                    del self.live[key]
            future.set_exception(e)
        return future


class Gate:
    """One screen in flight per person (a second request gets the same
    answer), and at most PER_MINUTE new ones a minute."""

    def __init__(self, clock=time_mod.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.flights: dict = {}  # person -> Future of the screen
        self.waiters: dict = {}  # person -> requests waiting on it
        self.recent: OrderedDict = OrderedDict()  # person -> deque of times

    def join_or_lead(self, key: str):
        """(future, leads): the screen in flight (call leave() when done
        waiting), or a new one this caller builds; (None, False) past the
        rate limit or MAX_WAITERS."""
        with self.lock:
            running = self.flights.get(key)
            if running is not None:
                if self.waiters.get(key, 0) >= MAX_WAITERS:
                    return None, False
                self.waiters[key] = self.waiters.get(key, 0) + 1
                return running, False
            now = self.clock()
            times = self.recent.setdefault(key, deque())
            self.recent.move_to_end(key)
            while times and now - times[0] > 60:
                times.popleft()
            if len(times) >= PER_MINUTE:
                return None, False
            times.append(now)
            while len(self.recent) > MAX_PEOPLE:
                self.recent.popitem(last=False)
            future: Future = Future()
            self.flights[key] = future
            return future, True

    def leave(self, key: str) -> None:
        with self.lock:
            left = self.waiters.get(key, 0) - 1
            if left > 0:
                self.waiters[key] = left
            else:
                self.waiters.pop(key, None)

    def land(self, key: str, future: Future) -> None:
        with self.lock:
            if self.flights.get(key) is future:
                del self.flights[key]


READS = Reads()


def section(state: str, items=(), cap: int = MAX_ROWS) -> dict:
    items = list(items)
    out = {"state": state, "items": items[:cap]}
    if len(items) > cap:
        out["more"] = True
    return out


# "3 PM" / "3:30 PM" (timeutil.spoken_time) -> "15:00"; "" if it isn't one
_SPOKEN = re.compile(r"^(\d{1,2})(?::(\d\d))? ([AP]M)$")


def clock_of(spoken: str) -> str:
    m = _SPOKEN.match(str(spoken or "").strip())
    if not m:
        return ""
    hour = int(m.group(1)) % 12 + (12 if m.group(3) == "PM" else 0)
    return f"{hour:02d}:{m.group(2) or '00'}"


def _day_of(left: int) -> str:
    return "overdue" if left < 0 else "today" if left == 0 else "tomorrow"


def _state_of(error: Exception) -> str:
    return "reconnect" if type(error).__name__ in RECONNECT else "error"


# ---------- the sections (each reads; none writes) ----------

def calendar_section(registry, account, today) -> dict:
    """Today's and tomorrow's events on the primary calendar."""
    from kwil.tools import google_auth
    from kwil.tools.timeutil import spoken_date

    tool = registry.tools.get("list_calendar_events") if registry is not None else None
    if tool is None:
        return section("off")
    if google_auth.needs_reconnect(account):
        return section("reconnect")  # Google refused the sign-in: don't ask again
    result = tool.handler(day=today.isoformat(), days=2)
    if not isinstance(result, dict):
        return section("error")
    tainted = bool(result.get(UNTRUSTED_MARK))
    tomorrow = today + timedelta(days=1)
    days = {"today": [], "tomorrow": []}
    for e in result.get("events") or []:
        if not isinstance(e, dict):
            continue
        day = tomorrow if e.get("date") == spoken_date(tomorrow) else today
        # started before today (e.g. last night until 1 AM, or a trip):
        # no start time, the end only for a timed one (as "until ...")
        continues = e.get("date") not in (spoken_date(today), spoken_date(tomorrow))
        # local wall times as ISO ("2027-01-14T15:00"): the page words them
        # in its own language
        start, end = clock_of(e.get("start")), clock_of(e.get("end"))
        if continues:
            start = ""
        days["tomorrow" if day == tomorrow else "today"].append({
            "id": str(e.get("id", "")), "title": str(e.get("title", "")),
            "day": "tomorrow" if day == tomorrow else "today",
            "start": f"{day.isoformat()}T{start}" if start else "",
            "end": f"{day.isoformat()}T{end}" if end else "",
            "continues": continues,
            "all_day": bool(e.get("all_day")), "location": e.get("location", ""),
            "outside": bool(e.get("from_others")) or tainted})
    # each day its own MAX_ROWS: a full today never hides tomorrow
    out = section("ok", days["today"][:MAX_ROWS] + days["tomorrow"][:MAX_ROWS],
                  cap=2 * MAX_ROWS)
    if any(len(v) > MAX_ROWS for v in days.values()):
        out["more"] = True
    return out


def notion_section(registry, today) -> dict:
    """Notion assignments due by tomorrow, overdue ones too (not Done)."""
    tool = registry.tools.get("list_assignments") if registry is not None else None
    if tool is None:
        return section("off")
    result = tool.handler(days_ahead=1)
    if not isinstance(result, dict):
        return section("error")
    items = []
    for a in result.get("assignments") or []:
        if not isinstance(a, dict):
            continue
        try:
            due = date.fromisoformat(str(a.get("due_date") or ""))
        except ValueError:
            continue
        # "Friday, January 15 at 3 PM" (NotionAssignments._summarize)
        at = clock_of(str(a.get("due") or "").rpartition(" at ")[2]) \
            if " at " in str(a.get("due") or "") else ""
        items.append({"id": str(a.get("id", "")), "title": str(a.get("name", "")),
                      "course": a.get("course", ""),
                      "due": f"{due.isoformat()}T{at}" if at else due.isoformat(),
                      "day": _day_of((due - today).days),
                      "outside": True})  # Notion titles are untrusted (family._notion_tools)
    return section("ok", items)


def deadlines_section(sync, uid, now: datetime) -> dict:
    """The deadline store's work due by tomorrow, and overdue (at most
    OVERDUE_DAYS back): every title came from Canvas, email or a page."""
    from kwil.proactive import parse_due

    if sync is None or not sync.listed(uid):
        return section("off")
    today = now.date()
    items = []
    for row in sync.store.list(uid):
        if row.get("kind") == "class" or row.get("status") in ("dismissed", "superseded"):
            continue
        due = parse_due(row.get("due"), now.tzinfo)
        if due is None:
            continue
        left = (due.date() - today).days
        if not -OVERDUE_DAYS <= left <= 1:
            continue
        timed = len(str(row.get("due") or "").strip()) > 10
        items.append({"id": row.get("id"), "title": row.get("title", ""),
                      "course": row.get("course", ""), "kind": row.get("kind", ""),
                      "day": _day_of(left),
                      # local, for the page to word: a date, or a date and time
                      "due": due.strftime("%Y-%m-%dT%H:%M") if timed
                      else due.date().isoformat(),
                      "outside": True})
    return section("ok", items)


def reminders_section(store, now: datetime, lang: str) -> dict:
    """Active, unpaused reminders whose next time is today here."""
    from kwil import reminders

    if store is None:
        return section("off")
    # local midnight to midnight: 23 or 25 hours on a clock-change day
    start = datetime.combine(now.date(), time.min, now.tzinfo).timestamp()
    end = datetime.combine(now.date() + timedelta(days=1), time.min, now.tzinfo).timestamp()
    items = []
    for r in store.list(finished=False):
        ts = r.get("next_ts")
        if r.get("paused") or ts is None or not start <= ts < end:
            continue
        p = reminders.public(r, now.timestamp(), lang)
        items.append({"id": p["id"], "title": p["text"], "when": p["next_local"],
                      "task": bool(p["prompt_task"]), "outside": bool(p["untrusted"])})
    return section("ok", items)


def research_section(service, uid) -> dict:
    """The latest research jobs: a finished one opens its conversation."""
    if service is None:
        return section("off")
    jobs = service.jobs_of(uid)
    if jobs is None:
        return section("off")
    return section("ok", [{"id": j["id"], "title": j.get("question", ""),
                           "status": j.get("status"),
                           "running": j.get("status") in RUNNING,
                           "conversation_id": j.get("conversation_id")}
                          for j in jobs[:MAX_RESEARCH]])


def study_section(store) -> dict:
    """Study mode's most missed concepts."""
    if store is None or not callable(getattr(store, "missed", None)):
        return section("off")
    return section("ok", [{"id": m["id"], "title": m.get("concept", ""),
                           "course": m.get("course", ""), "count": m.get("count", 0),
                           "outside": bool(m.get("untrusted"))}
                          for m in store.missed()[:MAX_STUDY]])


def brief_section(scheduler, uid) -> dict:
    if scheduler is None:
        return section("off")
    text = (scheduler.brief(uid) or {}).get("text") or ""
    return section("ok", [{"text": text}] if text else [])


# ---------- the whole screen ----------

def build(assistant, s, scheduler, student) -> dict:
    """Every section for session `s`, from one reading of the clock."""
    from kwil import chat as chat_mod
    from kwil.tools.timeutil import TURN_NOW

    tz = getattr(s.chat, "timezone", None)
    now = chat_mod._now(tz)  # once: today and tomorrow come from this
    today = now.date()
    uid = s.user
    registry = getattr(s, "registry", None)
    clock = TURN_NOW.set(now)
    try:
        person = uid or ""
        slow = {"calendar": READS.start((person, "calendar"), calendar_section,
                                        registry, uid, today),
                "notion": READS.start((person, "notion"), notion_section, registry, today)}
        deadline = time_mod.monotonic() + SLOW_SECONDS  # one wait for both

        def read(name, fn, *args):
            try:
                return fn(*args)
            except Exception as e:  # noqa: BLE001 - one section's problem stays its own
                log.warning("Today: %s failed (%s)", name, type(e).__name__)
                return section(_state_of(e))

        find = getattr(assistant, "reminder_store_of", None)
        memory = getattr(assistant, "memory_store_of", None)
        out = {
            "date": today.isoformat(), "tomorrow": (today + timedelta(days=1)).isoformat(),
            "brief": read("brief", brief_section, scheduler, uid),
            "deadlines": read("deadlines", deadlines_section, student, uid, now),
            "reminders": read("reminders", lambda: reminders_section(
                find(s) if callable(find) else None, now, s.language)),
            "research": read("research", research_section,
                             getattr(assistant, "research", None), uid),
            "study": read("study", lambda: study_section(
                memory(s) if callable(memory) else None)),
        }
        for name, future in slow.items():
            if future is None:
                log.warning("Today: %s not read (one still running)", name)
                out[name] = section("error")
                continue
            try:
                out[name] = future.result(timeout=max(0.0, deadline - time_mod.monotonic()))
            except FutureTimeout:
                log.warning("Today: %s took over %.0f s", name, SLOW_SECONDS)
                out[name] = section("error")
            except Exception as e:  # noqa: BLE001
                log.warning("Today: %s failed (%s)", name, type(e).__name__)
                out[name] = section(_state_of(e))
    finally:
        TURN_NOW.reset(clock)
    log.info("Today for %s: %s", uid or "owner", ", ".join(
        f"{k} {out[k]['state']} {len(out[k]['items'])}"
        for k in ("brief", "calendar", "deadlines", "notion", "reminders",
                  "research", "study")))
    return out


def register(app, assistant, session_of, error, scheduler) -> None:
    """GET /api/today (webapp.create_app, after proactive's routes: the
    brief is the proactive scheduler's)."""
    from fastapi import Request
    from fastapi.responses import JSONResponse
    from starlette.concurrency import run_in_threadpool

    family = getattr(assistant, "family", None)
    gate = Gate()
    app.state.today_gate = gate  # tests

    @app.get("/api/today")
    async def today(request: Request):
        s = session_of(request)
        if s is None:
            return error("not signed in", 401)
        if s.user is not None and (family is None or family.get(s.user) is None):
            return error("not found", 404)  # removed meanwhile: nothing of theirs
        student = getattr(app.state, "student", None)
        key = s.user or ""
        future, leads = gate.join_or_lead(key)
        if future is None:
            log.warning("Today: %s refused (over %d a minute, or %d waiting)", s.user or "owner",
                        PER_MINUTE, MAX_WAITERS)
            return error("too many requests; wait a minute", 429)
        if leads:
            try:
                data = await run_in_threadpool(build, assistant, s, scheduler, student)
                future.set_result(data)
            except BaseException as e:
                # a joiner gets a plain failure, never this request's own
                # cancellation (the client went away)
                future.set_exception(e if isinstance(e, Exception)
                                     else RuntimeError("the screen was abandoned"))
                raise
            finally:
                gate.land(key, future)
        else:  # the same person's screen in flight: its answer, no second read
            try:
                # on the event loop, no worker thread held; shield: our own
                # timeout never cancels the screen the first request builds
                data = await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(future)),
                                              SLOW_SECONDS + 5)
            except asyncio.TimeoutError:
                log.warning("Today: %s waited too long for the screen in flight",
                            s.user or "owner")
                return error("couldn't load Today; try again", 503)
            except Exception as e:  # noqa: BLE001 - the first request's failure
                log.warning("Today: the screen in flight failed (%s)", type(e).__name__)
                return error("couldn't load Today; try again", 503)
            finally:
                gate.leave(key)
        return JSONResponse(data, headers={"Cache-Control": "no-store"})
