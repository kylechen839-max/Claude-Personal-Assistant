"""The Today screen (web/today.js): one read-only look at a person's day.

  GET /api/today   {"date", "tomorrow", "brief", "calendar", "deadlines",
                    "notion", "reminders", "research", "study"}

Each section is {"state": "ok" | "off" | "reconnect" | "error", "items"}
with at most MAX_ROWS rows, and one failing section never hides the
others. Everything is the signed-in person's own (the owner's, or an
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

import contextvars
import logging
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime, time, timedelta

from kwil.tools import UNTRUSTED_MARK

log = logging.getLogger(__name__)

MAX_ROWS = 10        # per section
MAX_RESEARCH = 3
MAX_STUDY = 5
OVERDUE_DAYS = 14    # older overdue deadlines stay in Settings, not on Today
SLOW_SECONDS = 8.0   # a calendar or Notion read longer than this: "error"
RUNNING = ("queued", "planning", "researching", "writing")
# exception names that mean "sign in again" (as student_web._code)
RECONNECT = ("MissingScopes", "GoogleSignInError", "RefreshError", "NotionReconnect")

# calendar and Notion reads run side by side here: bounded, and a stuck
# call can hold at most this many threads
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="today")


def section(state: str, items=()) -> dict:
    return {"state": state, "items": list(items)[:MAX_ROWS]}


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
    tomorrow = spoken_date(today + timedelta(days=1))
    items = []
    for e in result.get("events") or []:
        if not isinstance(e, dict):
            continue
        items.append({
            "id": str(e.get("id", "")), "title": str(e.get("title", "")),
            "day": "tomorrow" if e.get("date") == tomorrow else "today",
            "start": e.get("start", ""), "end": e.get("end", ""),
            "all_day": bool(e.get("all_day")), "location": e.get("location", ""),
            "outside": bool(e.get("from_others")) or tainted})
    return section("ok", items)


def notion_section(registry) -> dict:
    """Notion assignments due by tomorrow, overdue ones too (not Done)."""
    tool = registry.tools.get("list_assignments") if registry is not None else None
    if tool is None:
        return section("off")
    result = tool.handler(days_ahead=1)
    if not isinstance(result, dict):
        return section("error")
    items = [{"id": str(a.get("id", "")), "title": str(a.get("name", "")),
              "course": a.get("course", ""), "due": a.get("due", ""),
              "due_date": a.get("due_date", ""), "overdue": bool(a.get("overdue")),
              "outside": True}  # Notion titles are untrusted (family._notion_tools)
             for a in result.get("assignments") or [] if isinstance(a, dict)]
    return section("ok", items)


def deadlines_section(sync, uid, now: datetime) -> dict:
    """The deadline store's work due by tomorrow, and overdue (at most
    OVERDUE_DAYS back): every title came from Canvas, email or a page."""
    from kwil.proactive import parse_due
    from kwil.tools.timeutil import spoken_time

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
                      "day": "overdue" if left < 0 else "today" if left == 0 else "tomorrow",
                      "time": spoken_time(due) if timed else "",
                      "outside": True})
    return section("ok", items)


def reminders_section(store, now: datetime, lang: str) -> dict:
    """Active, unpaused reminders whose next time is today here."""
    from kwil import reminders

    if store is None:
        return section("off")
    start = datetime.combine(now.date(), time.min, now.tzinfo).timestamp()
    end = start + 86400
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
        def start(fn, *args):
            # each in its own copy of this context (TURN_NOW, the language)
            return _POOL.submit(contextvars.copy_context().run, fn, *args)

        slow = {"calendar": start(calendar_section, registry, uid, today),
                "notion": start(notion_section, registry)}

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
            try:
                out[name] = future.result(timeout=SLOW_SECONDS)
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

    @app.get("/api/today")
    async def today(request: Request):
        s = session_of(request)
        if s is None:
            return error("not signed in", 401)
        if s.user is not None and (family is None or family.get(s.user) is None):
            return error("not found", 404)  # removed meanwhile: nothing of theirs
        student = getattr(app.state, "student", None)
        data = await run_in_threadpool(build, assistant, s, scheduler, student)
        return JSONResponse(data, headers={"Cache-Control": "no-store"})
