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
synced to the Pi or PC), replies, server sentences and speech in their
language (webapp.WebAssistant picks the Chinese Whisper model and voice),
and, once they tap "Connect Google account" on their own phone, their own
Google Calendar and Gmail (read, drafts only; deletes ask first) from
`<dir>/<id>/google_token.json` (google_web.py signs them in). Their token
is only ever read from that file, never from GOOGLE_TOKEN_JSON (Kyle's),
and Kyle's tools never read theirs. Nothing else: no Notion, Pinecone
memory or notes, Kyle's Google, dev team, lecture recorder or tutor, and no
way to see anyone else's history. Removing someone signs them out and
deletes their folder, Google token included.
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
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

TOKEN_PREFIX = "hcinv."
LANGUAGES = ("en", "zh")
MAX_NAME = 40
STATUSES = ("invited", "active")
GOOGLE_TOKEN = "google_token.json"  # in <dir>/<id>/
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
        # removed people whose folder isn't deleted yet (e.g. a file was
        # locked on Windows): kept in users.json and retried at startup, on
        # the next removal and when the owner opens the list
        self._deleting: set[str] = set()
        self._contexts: dict[str, UserContext] = {}
        self._load()
        self._finish_deletes()

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
            deleting = data.get("deleting", []) if isinstance(data, dict) else []
            self._deleting = {uid for uid in deleting if isinstance(uid, str)
                              and valid_id(uid) and uid not in self.users}
        except Exception as e:  # unreadable: nobody can sign in until fixed
            log.error("Family: ignoring unreadable %s (%s)", self.path, type(e).__name__)
            self.users = {}

    def _save(self, users: dict, deleting: set | None = None) -> None:
        """Write `users` (a new copy; the caller swaps it in only after this
        returns, so a failed write changes nothing and can be retried)."""
        from assistant.tools.google_auth import write_private

        deleting = self._deleting if deleting is None else deleting
        self.dir.mkdir(parents=True, exist_ok=True)
        write_private(self.path, json.dumps(
            {"users": users, "deleting": sorted(deleting)}, indent=1))

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
            users = {**self.users, uid: user}
            self._save(users)
            self.users = users
        log.info("Family: invited %s (%d people)", uid, len(self.users))
        return public(user), token

    def reinvite(self, uid: str) -> tuple[dict, str]:
        """A fresh link for someone (a new phone, or the old link expired);
        any earlier link stops working."""
        with self.lock:
            if uid not in self.users:
                raise FamilyError("not found", 404)
            user = dict(self.users[uid])
            token = self._new_link(user)
            users = {**self.users, uid: user}
            self._save(users)
            self.users = users
        log.info("Family: new invite link for %s", uid)
        return public(user), token

    def members(self) -> list[dict]:
        self._finish_deletes()
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
            found = {**found, "status": "active", "invite_hash": None,
                     "invite_expires": None}
            users = {**self.users, found["id"]: found}
            self._save(users)
            self.users = users
            result = create(found["id"])
        log.info("Family: %s accepted an invite", found["id"])
        return result

    def remove(self, uid: str, before_delete: Callable[[str], None] | None = None) -> bool:
        """Remove someone (invited or active): their entry, then
        `before_delete(uid)` (sign them out), then their history and folder."""
        with self.lock:
            if uid not in self.users:
                return False
            users = {k: v for k, v in self.users.items() if k != uid}
            deleting = self._deleting | {uid} if valid_id(uid) else self._deleting
            self._save(users, deleting)  # OSError: nothing changed, try again
            self.users, self._deleting = users, deleting
            ctx = self._contexts.pop(uid, None)
            if before_delete is not None:
                before_delete(uid)
        if ctx is not None:
            ctx.close()
        self._finish_deletes()
        log.info("Family: removed %s (%d people left)", uid, len(self.users))
        return True

    def _finish_deletes(self) -> None:
        """Delete the folders of people already removed from the list; one
        that can't be deleted now stays listed in users.json for next time."""
        with self.lock:
            pending = sorted(self._deleting)
        if not pending:
            return
        done = set()
        for uid in pending:
            try:
                _rmtree(self.dir / uid)
                done.add(uid)
            except OSError:
                log.error("Family: %s's folder couldn't be deleted yet; "
                          "will retry", uid)
        if not done:
            return
        with self.lock:
            self._deleting = self._deleting - done
            try:
                self._save(self.users)
            except OSError as e:  # listed again next start: rmtree is idempotent
                log.error("Family: couldn't save %s (%s)", self.path, type(e).__name__)

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
        """Web search and fetch, and their own Google once connected. No
        browser (it would open on the server), no Notion, memory, notes,
        history, Kyle's Google or dev team."""
        registry.register(*self._google_tools(user))
        web = self.config["web"]
        if not web["enabled"]:
            return
        from assistant.tools import web as web_tools

        # their time zone, never Kyle's city
        cfg = {**web, "location": {"timezone": user.get("timezone") or ""}}
        registry.add_server_tools(
            *web_tools.server_tools(self.config["assistant"]["model"], cfg))

    # ---------- their own Google (google_web.py signs them in) ----------
    def google_token(self, uid: str) -> Path:
        return self.dir / uid / GOOGLE_TOKEN

    def google_client(self) -> dict | None:
        """The Web application OAuth client they sign in with; None (their
        Google is off) if it isn't set up."""
        from assistant.tools.google_auth import web_client_config

        try:
            client = web_client_config(self.config["family"]["google_client"])
        except (OSError, ValueError) as e:  # never the value
            log.warning("Family: the Google web client is unreadable (%s)", e)
            return None
        if client is not None and "web" not in client:
            log.warning("Family: the Google client is not a Web application client")
            return None
        return client

    def google_enabled(self) -> bool:
        return bool(self.config["family"]["google"] and own_origin(self.config)
                    and self.google_client())

    def google_connected(self, uid: str) -> bool:
        return valid_id(uid) and self._google_info(uid) is not None

    def _google_info(self, uid: str) -> dict | None:
        from assistant.tools.google_auth import load_token_info

        try:  # environ={}: their file only, never GOOGLE_TOKEN_JSON
            return load_token_info(self.google_token(uid), environ={})
        except (OSError, ValueError):
            return None

    def _google_tools(self, user: dict) -> list:
        """Calendar and Gmail on their own token (none until they connect).
        Never Kyle's: `environ={}` hides GOOGLE_TOKEN_JSON/GOOGLE_CLIENT_JSON,
        `save_token=False` never starts a sign-in on the server, `in_app`
        makes an expired or revoked token say "reconnect in the app", and
        the calendar is their primary one."""
        if not (self.config["family"]["google"] and valid_id(user.get("id"))):
            return []
        info = self._google_info(user["id"])
        if info is None:
            return []
        from assistant.tools.calendar import GoogleCalendar
        from assistant.tools.google_auth import GMAIL_SCOPES, granted_scopes

        token, tz = self.google_token(user["id"]), user.get("timezone") or None
        tools = GoogleCalendar(credentials_path="", token_path=token,
                               calendar_id="primary", timezone=tz,
                               save_token=False, environ={}, in_app=True).tools()
        if self.config["gmail"]["enabled"] and \
                set(GMAIL_SCOPES) <= granted_scopes(info):
            from assistant.tools.gmail import GmailInbox

            tools += GmailInbox(credentials_path="", token_path=token, timezone=tz,
                                max_summary=self.config["gmail"]["max_summary"],
                                save_token=False, environ={}, in_app=True).tools()
        # the descriptions say "Kyle's calendar": make them about this person
        name = "the user" if user.get("language") == "zh" else user["name"]
        return [replace(t, description=t.description.replace("Kyle's", f"{name}'s")
                        .replace("Kyle", name)) for t in tools]

    def save_google(self, uid: str, token_text: str) -> bool:
        """Store their token (privately, atomically) and add their Google
        tools to their open conversations. False if they were removed
        meanwhile (nothing written: their folder is going away)."""
        from assistant.tools.google_auth import write_private

        with self.lock:
            user = self.users.get(uid)
            if user is None or user.get("status") != "active" or not valid_id(uid):
                return False
            path = self.google_token(uid)
            path.parent.mkdir(parents=True, exist_ok=True)
            write_private(path, token_text)
            self._refresh_tools(uid)
        log.info("Family: %s connected their Google account", uid)
        return True

    def disconnect_google(self, uid: str) -> bool:
        """Delete their token and take their Google tools away. False if
        there was nothing to delete."""
        with self.lock:
            if uid not in self.users or not valid_id(uid):
                return False
            path = self.google_token(uid)
            had = path.exists()
            _unlink(path)
            self._refresh_tools(uid)
        log.info("Family: %s disconnected their Google account", uid)
        return had

    def _refresh_tools(self, uid: str) -> None:
        """Their open conversations' tools after a connect or disconnect:
        every SessionTools shares this registry's dict, so update it in
        place, adding before removing (a running turn never sees it empty)."""
        ctx = self._contexts.get(uid)
        if ctx is None:
            return
        from assistant.tools import ToolRegistry

        fresh = ToolRegistry()
        self._register_tools(ctx.user, fresh)
        ctx.registry.tools.update(fresh.tools)
        for name in set(ctx.registry.tools) - set(fresh.tools):
            ctx.registry.tools.pop(name, None)

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
                              timezone=user.get("timezone") or None,
                              fast_model=a["fast_model"], router=a["router"])

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


def _unlink(path: Path, attempts: int = 20) -> None:
    """Delete a file; retried like _rmtree (a reader may hold it on Windows)."""
    for attempt in range(attempts):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.05)


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
                raise
            time.sleep(0.1)


# ---------- HTTP (added by webapp.create_app) ----------

def invite_url(origin: str, token: str) -> str:
    """The link to send: the token rides in the #fragment, which browsers
    never send to a server (family.js reads it, clears it and POSTs it)."""
    return f"{origin.rstrip('/')}/#invite={token}"


def own_origin(config: dict) -> str:
    """[webapp] public_origin, else (with failover) this backend's own URL
    (the cloud's defaults to APP_ORIGIN); "" = unknown."""
    configured = config["webapp"].get("public_origin") or ""
    if configured:
        return configured
    try:
        from assistant.failover.peers import settings_from_config

        settings = settings_from_config(config)
    except Exception:  # a bad [failover] section is reported elsewhere
        return ""
    own = [b.url for b in settings.backends if settings.role and b.role == settings.role]
    return own[0] if own else ""


def register(app, family: "Family | None", web, assistant, error) -> None:
    """The owner's family routes and the public invite acceptance:

      POST   /api/invites             {name, language, timezone?} -> {url, expires_at}
      GET    /api/users               the invited people (owner only)
      POST   /api/users/{id}/remove   sign them out, delete their history
                                      (also DELETE /api/users/{id}; the page
                                      posts: the failover preflight allows
                                      only GET and POST cross-origin)
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
        """This server's own address (where /api/invite/accept is
        same-origin), never the page's: with failover the owner's page may
        be the Pi's or the app origin, calling here cross-origin."""
        return own_origin(family.config) or f"https://{request.url.netloc}"

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

    @app.post("/api/users/{uid}/remove")
    @app.delete("/api/users/{uid}")
    async def remove_user(uid: str, session=Depends(webapp.require_owner)):
        if (e := off()) is not None:
            return e
        try:
            removed = await run_in_threadpool(family.remove, uid, sign_out)
        except OSError:
            return error("couldn't remove them; try again", 500)
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
