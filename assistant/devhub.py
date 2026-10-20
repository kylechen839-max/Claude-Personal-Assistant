"""The PC hub: a desktop-only page on this PC's web app where Kyle chats
with his /build-feature coordinators (docs/COORDINATOR_LINK.md).

Sending uses devteam.CoordinatorLink (the Claude Code CLI, on his plan, no
API key); replies, pickup and presence come back through the same Notion
inbox, read with NOTION_TOKEN. The hub has its own link and state file
(data/devhub.json) and `hub-` reply ids, so the voice tools never pick up a
hub reply and the hub never takes a voice one.

  GET  /devteam, /devteam.js, /devteam.css   the page (no data in them)
  GET  /api/devteam/state                    windows, transcripts, presence
  POST /api/devteam/send   {session, text}   one message in flight per window
  POST /api/devteam/clear  {session}         deletes that window's transcript

Every route answers only a request from this machine itself (loopback peer,
no proxy headers), and the /api routes need the web app's sign-in.
"""

import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from assistant.devteam import (HUB_REPLY_CHARS, SOURCES, CoordinatorLink,
                               DevTeamError, link_from_config, session_id)

log = logging.getLogger(__name__)

WEB = Path(__file__).resolve().parent / "web"
MAX_WINDOWS = 8
MAX_MESSAGES = 200        # per window, oldest dropped first
MAX_TEXT = SOURCES["hub"][2]
IN_TASK_S = 5 * 60        # presence "working" this fresh: In task
MAYBE_S = 30 * 60         # ... this fresh: In task (no update ...); older: Idle


def hub_sessions(team: dict) -> list[str]:
    """The hub's windows: [devteam] hub_sessions (a list; one string is one
    id, never its characters), else coordinator_session. Valid, unique ids
    only, at most MAX_WINDOWS."""
    given = team.get("hub_sessions") or []
    if isinstance(given, str):
        given = [given]
    if not given and team.get("coordinator_session"):
        given = [team["coordinator_session"]]
    out = []
    for value in given:
        sid = session_id(value) if isinstance(value, str) else ""
        if not sid:
            log.warning("Hub: skipped a [devteam] hub_sessions entry that is "
                        "not a session id")
        elif sid not in out:
            out.append(sid)
    return out[:MAX_WINDOWS]


def enabled(config: dict, host: str) -> bool:
    """The hub runs only when asked for and only on a loopback bind (so
    never on the cloud server profile)."""
    from assistant.webapp import is_loopback

    return bool(config["devteam"].get("hub")) and is_loopback(host)


def presence_state(p: dict, now: float) -> dict:
    """The pill: task (working, fresh), maybe (working, 5-30 min old), idle
    (anything else with a row) or unknown (no row)."""
    status = p.get("status") or ""
    try:
        at = datetime.fromisoformat(p["at"].replace("Z", "+00:00")).timestamp()
    except (KeyError, AttributeError, ValueError):
        at = None
    age = None if at is None else max(0, int(now - at))
    if not status:
        state = "unknown"
    elif status == "working" and age is not None and age <= IN_TASK_S:
        state = "task"
    elif status == "working" and age is not None and age <= MAYBE_S:
        state = "maybe"
    else:
        state = "idle"
    return {"state": state, "text": p.get("text", "") if state == "task" else "",
            "age": age}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Hub:
    """The windows, their transcripts and a send lock per window. The
    transcript lives in the hub link's own state file, written privately
    and atomically with the link's pending reply ids."""

    def __init__(self, config: dict, link: CoordinatorLink | None = None,
                 clock=time.time):
        team = config["devteam"]
        self.sessions = hub_sessions(team)
        self.poll_seconds = max(1.0, float(team.get("poll_seconds") or 4))
        self.link = link or link_from_config(
            config, team["hub_state_file"],
            coordinator=self.sessions[0] if self.sessions else "")
        self.clock = clock
        self._send_locks = {s: threading.Lock() for s in self.sessions}
        self._poll_lock = threading.Lock()
        self._last_poll = -1e18
        self._presence: dict[str, dict] = {}
        self._notion_error = ""
        with self.link._lock:
            windows = self.link._state.setdefault("windows", {})
            gone = [sid for sid in windows if sid not in self.sessions]
            for sid in gone:  # a window no longer configured: gone, on disk too
                for rid in {m.get("reply_id") for m in windows.pop(sid)} - {None}:
                    self.link._state["pending"].pop(rid, None)
                    self.link._state.get("seen", {}).pop(rid, None)
            for sid in self.sessions:
                windows.setdefault(sid, [])
            if gone:
                self.link._save()
                log.info("Hub: removed %d window(s) no longer configured", len(gone))

    # ----- transcript -----
    def _messages(self, sid: str) -> list:
        return self.link._state["windows"].setdefault(sid, [])

    def _add(self, sid: str, message: dict) -> None:
        msgs = self._messages(sid)
        msgs.append(message)
        del msgs[:-MAX_MESSAGES]

    def send(self, sid: str, text: str) -> dict:
        """Queue Kyle's message into window `sid`'s session. Raises
        DevTeamError (recorded as "not sent") or BlockingIOError when a
        send for this window is already in flight."""
        lock = self._send_locks[sid]
        if not lock.acquire(blocking=False):
            raise BlockingIOError
        try:
            text = (text or "").strip()[:MAX_TEXT]
            msg = {"from": "kyle", "text": text, "at": _now()}
            try:
                sent = self.link.send(text, source="hub", session=sid)
            except DevTeamError as e:
                msg.update(stage="not sent", error=str(e))
                log.info("Hub message not sent (%d characters)", len(text))
            else:
                msg.update(stage="queued", reply_id=sent["reply_id"])
                log.info("Hub message %s queued (%d characters)",
                         sent["reply_id"], len(text))
            with self.link._lock:
                self._add(sid, msg)
                self.link._save()
            return msg
        finally:
            lock.release()

    def clear(self, sid: str) -> None:
        """Delete the window's transcript and forget its pending replies, so
        a late answer to a cleared message never comes back."""
        with self.link._lock:
            ids = {m.get("reply_id") for m in self._messages(sid)} - {None}
            self.link._state["windows"][sid] = []
            for rid in ids:
                self.link._state["pending"].pop(rid, None)
                self.link._state.get("seen", {}).pop(rid, None)
            self.link._save()
        log.info("Hub window cleared (%d pending replies dropped)", len(ids))

    # ----- polling Notion -----
    def poll(self) -> None:
        """Read new replies and the presence rows, at most once every
        poll_seconds however many pages ask (blocking: run it in a thread)."""
        if not self.sessions or not self._poll_lock.acquire(blocking=False):
            return
        try:
            if self.clock() - self._last_poll < self.poll_seconds:
                return
            self._last_poll = self.clock()
            found = (self.link.replies(max_chars=HUB_REPLY_CHARS)
                     if self.link.pending() else [])
            self._presence = self.link.presences(self.sessions)
            self._notion_error = ""
        except DevTeamError as e:
            self._notion_error = str(e)
            return
        except Exception as e:  # Notion down, no token, no access
            log.warning("Hub: Notion inbox unreadable: %s", type(e).__name__)
            self._notion_error = "Couldn't read the coordinator inbox in Notion."
            return
        finally:
            self._poll_lock.release()
        if found:
            self._apply(found)

    def _apply(self, found: list[dict]) -> None:
        by_id = {r["reply_id"]: r for r in found}
        with self.link._lock:
            for sid in self.sessions:
                msgs = self._messages(sid)
                for m in list(msgs):
                    r = by_id.get(m.get("reply_id"))
                    if not r:
                        continue
                    if r["status"] == "working":
                        m.update(stage="working", step=r["reply"][:200])
                        continue
                    status = "blocked" if r["status"] == "blocked" else "answered"
                    m.update(stage=status)
                    m.pop("step", None)
                    self._add(sid, {"from": "coordinator", "text": r["reply"],
                                    "at": _now(), "status": status,
                                    "reply_id": r["reply_id"],
                                    "run_url": r.get("run_url", "")})
                    log.info("Hub reply %s: %s (%d characters)", r["reply_id"],
                             status, len(r["reply"]))
            self.link._save()

    def state(self) -> dict:
        now = self.clock()
        with self.link._lock:
            windows = []
            for sid in self.sessions:
                msgs = [dict(m) for m in self._messages(sid)]
                for m in msgs:  # coordinator ids stay server-side
                    m.pop("reply_id", None)
                windows.append({
                    "session": sid, "url": f"https://claude.ai/code/{sid}",
                    "presence": presence_state(self._presence.get(sid, {}), now),
                    "queued": any(m.get("stage") == "queued" for m in msgs),
                    "messages": msgs})
        return {"windows": windows, "poll_seconds": self.poll_seconds,
                "notion_error": self._notion_error, "max_text": MAX_TEXT}


def from_this_machine(request) -> bool:
    """A loopback peer with no proxy headers (a `tailscale serve` or other
    proxy hop adds them), and no configured trusted proxy vouching for it."""
    from assistant.webapp import is_loopback

    client = request.client.host if request.client else ""
    return (is_loopback(client) and "x-forwarded-for" not in request.headers
            and "tailscale-user-login" not in request.headers
            and not request.app.state.web.trusted_proxy.trusts(request))


def install(app, hub: Hub) -> None:
    """Add the hub's routes to a create_app() app (same sign-in, CSP and
    guard middleware as the rest)."""
    from fastapi import Depends, HTTPException, Request
    from fastapi.responses import FileResponse, JSONResponse
    from starlette.concurrency import run_in_threadpool

    from assistant.webapp import require_owner

    def local_only(request: Request) -> None:
        """404 for anything not from this machine itself."""
        if not from_this_machine(request):
            raise HTTPException(404, "not found")

    local = [Depends(local_only)]

    def error(message: str, status: int):
        return JSONResponse({"error": message}, status)

    async def body_of(request: Request) -> dict:
        try:
            body = await request.json()
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}

    def window(body: dict) -> str:
        sid = body.get("session")
        return sid if isinstance(sid, str) and sid in hub.sessions else ""

    # The page and its code hold no data; the sign-in is asked for by the
    # page itself (through /api/login) when /api/devteam/state says 401.
    @app.get("/devteam", dependencies=local)
    def page():
        return FileResponse(WEB / "devteam.html", media_type="text/html")

    @app.get("/devteam.js", dependencies=local)
    def page_js():
        return FileResponse(WEB / "devteam.js", media_type="text/javascript")

    @app.get("/devteam.css", dependencies=local)
    def page_css():
        return FileResponse(WEB / "devteam.css", media_type="text/css")

    @app.get("/api/devteam/state", dependencies=local)
    async def state(session=Depends(require_owner)):
        await run_in_threadpool(hub.poll)
        return hub.state()

    @app.post("/api/devteam/send", dependencies=local)
    async def send(request: Request, session=Depends(require_owner)):
        body = await body_of(request)
        sid = window(body)
        text = body.get("text")
        if not sid:
            return error("no such window", 404)
        if not isinstance(text, str) or not text.strip():
            return error("empty message", 400)
        try:
            msg = await run_in_threadpool(hub.send, sid, text)
        except BlockingIOError:
            return error("one message at a time", 409)
        if msg.get("stage") == "not sent":
            return JSONResponse({"error": msg["error"], "message": msg}, 502)
        return {"message": msg}

    @app.post("/api/devteam/clear", dependencies=local)
    async def clear(request: Request, session=Depends(require_owner)):
        sid = window(await body_of(request))
        if not sid:
            return error("no such window", 404)
        await run_in_threadpool(hub.clear, sid)
        return {"ok": True}
