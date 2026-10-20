"""Family accounts on the cloud server: Kyle invites someone, they get their
own conversation history and language, and none of Kyle's things.

`[family] enabled` turns it on (the server profile does). The owner (who
signs in with WEB_TOKEN) taps "Invite someone" in the phone app: that makes
a one-time link `https://<origin>/#invite=hcinv.<token>`, valid for
`invite_days`. Opening it signs that phone in as the new person
(POST /api/invite/accept; webapp.py). Only the SHA-256 of the token is kept,
in `<dir>/users.json`; the token itself is shown once and never logged.

What a family member gets (UserContext): web search and fetch (Anthropic's
server tools), their own history at `<dir>/<id>/history.sqlite` (never
synced to the Pi or PC), replies in their language. Nothing else: no
Notion, Pinecone memory or notes, Google, dev team, lecture recorder or
tutor, and no way to see anyone else's history. Removing someone signs them
out and deletes their folder.

Piece B (per-user Google, speech language) adds to `Family._register_tools`
and `UserContext`; nothing here needs to change shape for it.
"""

# No `from __future__ import annotations`: FastAPI must see the real
# Request class in the route signatures (imported inside register()).
import hashlib
import hmac
import json
import logging
import re
import secrets
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

TOKEN_PREFIX = "hcinv."
LANGUAGES = ("en", "zh")
MAX_NAME = 40
STATUSES = ("invited", "active")
_ID = re.compile(r"^u_[A-Za-z0-9_-]{8,40}$")


class FamilyError(ValueError):
    """A request the owner can fix (bad name, too many people...)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def hash_token(token: str) -> str:
    return hashlib.sha256(token.strip().encode()).hexdigest()


def valid_id(uid: str) -> bool:
    return bool(_ID.match(str(uid or "")))


def clean_name(name) -> str:
    """A display name: printable, one line, at most MAX_NAME characters."""
    if not isinstance(name, str):
        raise FamilyError("give a name")
    name = re.sub(r"\s+", " ", "".join(c for c in name if c.isprintable())).strip()
    if not name:
        raise FamilyError("give a name")
    if len(name) > MAX_NAME:
        raise FamilyError(f"the name can be at most {MAX_NAME} characters")
    return name


def clean_language(language) -> str:
    lang = str(language or "en").lower()
    lang = "zh" if lang.startswith("zh") else lang
    if lang not in LANGUAGES:
        raise FamilyError("language must be en or zh")
    return lang


def clean_timezone(tz) -> str:
    """An IANA zone name ("" = the server's)."""
    tz = str(tz or "").strip()
    if not tz:
        return ""
    if len(tz) > 64:
        raise FamilyError("unknown time zone")
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(tz)
    except Exception:
        raise FamilyError("unknown time zone") from None
    return tz


def public(user: dict) -> dict:
    """A user as the owner's page sees it (never the invite hash)."""
    return {k: user.get(k) for k in ("id", "name", "language", "timezone",
                                     "created", "status", "invite_expires")}


@dataclass
class UserContext:
    """One family member's own tools, history and chat maker."""

    user: dict
    registry: object                       # ToolRegistry (shared tool defs)
    history: object | None                 # HistoryStore, or None (history off)
    chat_factory: Callable = field(repr=False)

    @property
    def language(self) -> str:
        return self.user.get("language", "en")

    def close(self) -> None:
        if self.history is not None:
            try:
                self.history.close()
            except Exception:
                log.exception("Family: closing a history store failed")


class Family:
    """The invited people, kept in `<dir>/users.json` (written privately and
    atomically). Thread-safe: one lock covers the file, the contexts and
    redeeming, so an invite can't be accepted while it is being removed."""

    def __init__(self, config: dict, client=None, clock=time.time):
        f = config["family"]
        self.config = config
        self.dir = Path(f["dir"])
        self.path = self.dir / "users.json"
        self.max_users = int(f["max_users"])
        self.invite_days = float(f["invite_days"])
        self.clock = clock
        self._client = client
        self.lock = threading.RLock()
        self.users: dict[str, dict] = {}
        self._contexts: dict[str, UserContext] = {}
        self._load()

    # ---------- the file ----------
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            users = data.get("users", {}) if isinstance(data, dict) else {}
            self.users = {uid: u for uid, u in users.items()
                          if valid_id(uid) and isinstance(u, dict)
                          and u.get("status") in STATUSES}
        except Exception as e:  # unreadable: nobody can sign in until fixed
            log.error("Family: ignoring unreadable %s (%s)", self.path, type(e).__name__)
            self.users = {}

    def _save(self) -> None:
        from assistant.tools.google_auth import write_private

        self.dir.mkdir(parents=True, exist_ok=True)
        write_private(self.path, json.dumps({"users": self.users}, indent=1))

    # ---------- the owner's actions ----------
    def _new_link(self, user: dict) -> str:
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        user["invite_hash"] = hash_token(token)
        user["invite_expires"] = int(self.clock() + self.invite_days * 86400)
        return token

    def invite(self, name, language="en", timezone="") -> tuple[dict, str]:
        """A new person and their one-time token (shown once, never stored)."""
        user = {"name": clean_name(name), "language": clean_language(language),
                "timezone": clean_timezone(timezone)}
        with self.lock:
            if len(self.users) >= self.max_users:
                raise FamilyError(f"at most {self.max_users} people", 409)
            uid = "u_" + secrets.token_urlsafe(9)
            user.update(id=uid, created=int(self.clock()), status="invited")
            token = self._new_link(user)
            self.users[uid] = user
            self._save()
        log.info("Family: invited %s (%d people)", uid, len(self.users))
        return public(user), token

    def reinvite(self, uid: str) -> tuple[dict, str]:
        """A fresh link for someone (a new phone, or the old link expired);
        any earlier link stops working."""
        with self.lock:
            user = self.users.get(uid)
            if user is None:
                raise FamilyError("not found", 404)
            token = self._new_link(user)
            self._save()
        log.info("Family: new invite link for %s", uid)
        return public(user), token

    def members(self) -> list[dict]:
        with self.lock:
            return [public(u) for u in sorted(self.users.values(),
                                              key=lambda u: u.get("created", 0))]

    def get(self, uid: str | None) -> dict | None:
        with self.lock:
            user = self.users.get(uid) if uid else None
            return dict(user) if user else None

    def redeem(self, token: str, create: Callable[[str], object]):
        """Accept an invite: `create(uid)` (e.g. the new web session) runs
        with the lock held, so a DELETE can't slip in between. Returns its
        result, or None for an unknown, expired or used token."""
        if not isinstance(token, str) or not token.startswith(TOKEN_PREFIX) \
                or len(token) > 200:
            return None
        digest = hash_token(token)
        now = self.clock()
        with self.lock:
            found = None
            for user in self.users.values():  # compare every one, in constant time
                if hmac.compare_digest(user.get("invite_hash") or "", digest):
                    found = user
            if found is None or not found.get("invite_hash") \
                    or found.get("invite_expires", 0) < now:
                return None
            found["status"] = "active"
            found["invite_hash"] = None
            found["invite_expires"] = None
            self._save()
            result = create(found["id"])
        log.info("Family: %s accepted an invite", found["id"])
        return result

    def remove(self, uid: str, before_delete: Callable[[str], None] | None = None) -> bool:
        """Remove someone (invited or active): their entry, then
        `before_delete(uid)` (sign them out), then their history and folder."""
        with self.lock:
            if self.users.pop(uid, None) is None:
                return False
            self._save()
            ctx = self._contexts.pop(uid, None)
            if before_delete is not None:
                before_delete(uid)
        if ctx is not None:
            ctx.close()
        folder = self.dir / uid
        if valid_id(uid) and folder.exists():
            _rmtree(folder)
        log.info("Family: removed %s (%d people left)", uid, len(self.users))
        return True

    # ---------- what a family member's conversations use ----------
    def context(self, uid: str) -> UserContext | None:
        """Their tools, history and chat factory (made once, then cached);
        None for someone who isn't (or is no longer) listed."""
        with self.lock:
            user = self.users.get(uid)
            if user is None or user.get("status") != "active":
                return None
            ctx = self._contexts.get(uid)
            if ctx is None:
                ctx = self._contexts[uid] = self._build(dict(user))
            return ctx

    def _register_tools(self, user: dict, registry) -> None:
        """Web search and fetch only: no browser (it would open on the
        server), no Notion, memory, notes, history, Google or dev team.
        Piece B adds the person's own Google tools here."""
        web = self.config["web"]
        if not web["enabled"]:
            return
        from assistant.tools import web as web_tools

        # their time zone, never Kyle's city
        cfg = {**web, "location": {"timezone": user.get("timezone") or ""}}
        registry.add_server_tools(
            *web_tools.server_tools(self.config["assistant"]["model"], cfg))

    def _build(self, user: dict) -> UserContext:
        from assistant.chat import ClaudeChat
        from assistant.history import HistoryStore, Recorder, origin_of
        from assistant.tools import ToolRegistry

        a, h = self.config["assistant"], self.config["history"]
        registry = ToolRegistry()
        self._register_tools(user, registry)
        store = None
        if h["enabled"]:
            # Their own file, opened directly: history.open_store() would
            # replace the owner's store that record_event() writes to.
            # eventlog=False: nothing to sync to Kyle's Pi or PC.
            store = HistoryStore(self.dir / user["id"] / "history.sqlite",
                                 retention_days=h["retention_days"],
                                 max_mb=h["max_mb"], origin=origin_of(self.config),
                                 eventlog=False)

        def chat_factory(session_registry):
            recorder = Recorder(store, "web", client_fn=self.client,
                                title_model=h["title_model"])
            return ClaudeChat(client=self.client(), model=a["model"],
                              max_tokens=a["max_tokens"],
                              max_history=a["max_history"], tools=session_registry,
                              max_tool_rounds=a["max_tool_rounds"], memory=None,
                              recorder=recorder, language=user["language"],
                              user_name=user["name"],
                              timezone=user.get("timezone") or None)

        return UserContext(user, registry, store, chat_factory)

    def client(self):
        if self._client is None:
            import anthropic

            a = self.config["assistant"]
            self._client = anthropic.Anthropic(max_retries=a["api_max_retries"],
                                               timeout=a["api_timeout"])
        return self._client

    def close(self) -> None:
        with self.lock:
            contexts, self._contexts = list(self._contexts.values()), {}
        for ctx in contexts:
            ctx.close()


def _rmtree(folder: Path, attempts: int = 20) -> None:
    """Delete a folder; on Windows a file another thread just closed can be
    locked for a moment, so retry."""
    for attempt in range(attempts):
        try:
            shutil.rmtree(folder)
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt == attempts - 1:
                log.exception("Family: could not delete a user folder")
                raise
            time.sleep(0.1)


# ---------- HTTP (added by webapp.create_app) ----------

def invite_url(origin: str, token: str) -> str:
    """The link to send: the token rides in the #fragment, which browsers
    never send to a server (family.js reads it, clears it and POSTs it)."""
    return f"{origin.rstrip('/')}/#invite={token}"


def register(app, family: "Family | None", web, assistant, error) -> None:
    """The owner's family routes and the public invite acceptance:

      POST   /api/invites             {name, language, timezone?} -> {url, expires_at}
      GET    /api/users               the invited people (owner only)
      DELETE /api/users/{id}          sign them out, delete their history
      POST   /api/users/{id}/invite   a new link (the old one stops working)
      POST   /api/invite/accept       {invite} -> session cookie (public)

    Owner routes answer 404 to a family member (and with [family] off).
    Wrong, expired or used invites count toward the sign-in lockout."""
    from fastapi import Depends, Request
    from fastapi.responses import JSONResponse
    from starlette.concurrency import run_in_threadpool

    from assistant import webapp

    async def body_of(request: Request) -> dict:
        try:
            body = await request.json()
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}

    def origin_of(request: Request) -> str:
        configured = family.config["webapp"].get("public_origin") if family else ""
        if configured:
            return configured
        # same-origin POST from the page: the browser's Origin is this site
        origin = request.headers.get("origin")
        if origin and origin.startswith("https://"):
            return origin
        return f"https://{request.url.netloc}"

    def link(request: Request, user: dict, token: str):
        return {"user": user, "url": invite_url(origin_of(request), token),
                "expires_at": user["invite_expires"]}

    def off():
        return error("not found", 404) if family is None else None

    @app.post("/api/invites")
    async def create_invite(request: Request, session=Depends(webapp.require_owner)):
        if (e := off()) is not None:
            return e
        body = await body_of(request)
        try:
            user, token = await run_in_threadpool(
                family.invite, body.get("name"), body.get("language") or "en",
                body.get("timezone") or "")
        except FamilyError as e:
            return error(str(e), e.status)
        return link(request, user, token)

    @app.get("/api/users")
    def list_users(session=Depends(webapp.require_owner)):
        if (e := off()) is not None:
            return e
        return {"users": family.members(), "max_users": family.max_users}

    @app.post("/api/users/{uid}/invite")
    async def new_link(uid: str, request: Request,
                       session=Depends(webapp.require_owner)):
        if (e := off()) is not None:
            return e
        try:
            user, token = await run_in_threadpool(family.reinvite, uid)
        except FamilyError as e:
            return error(str(e), e.status)
        return link(request, user, token)

    def sign_out(uid: str) -> None:
        """Called with the family lock held, before their files go."""
        sids = web.store.revoke_user(uid)
        live = getattr(app.state, "voice_conversations", {})
        for sid in sids:
            entry = live.get(sid)
            if entry is not None:
                try:
                    entry[0].end("signed_out")
                except Exception:
                    log.exception("Family: hanging up a voice conversation failed")
        assistant.drop_user(uid)

    @app.delete("/api/users/{uid}")
    async def remove_user(uid: str, session=Depends(webapp.require_owner)):
        if (e := off()) is not None:
            return e
        try:
            removed = await run_in_threadpool(family.remove, uid, sign_out)
        except OSError:
            return error("couldn't delete their files; try again", 500)
        if not removed:
            return error("not found", 404)
        return {"ok": True}

    @app.post("/api/invite/accept")
    async def accept(request: Request):
        if (e := off()) is not None:
            return e
        given = (await body_of(request)).get("invite")
        sid = await run_in_threadpool(family.redeem, given, web.store.create)
        if sid is None:  # wrong, expired or already used: like a wrong token
            ip = web.client_ip(request)
            if web.limiter.blocked(ip):
                return error("too many attempts; try again later", 429)
            delay = web.limiter.fail(ip)
            log.warning("Web: a wrong, expired or used invite")
            if delay:
                await webapp._wait(delay)
            return error("this invite link has expired or was already used", 401)
        response = JSONResponse({"ok": True})
        webapp.set_session_cookie(response, web.token, sid, web.store.epoch)
        log.info("Web: family sign-in ok (%s)", web.store.user(sid))
        return response
