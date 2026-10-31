"""Connect a Google account from the phone app.

Two kinds of account, one sign-in flow:

- The owner's ([webapp] google_connect), for an instance whose user never
  touches the PC (e.g. config.mom.toml): the token goes to [calendar] token
  and Calendar, Gmail (read, drafts) and Drive (read-only) join the
  shared tools.
- The cloud server owner's ([webapp] owner_google, on in the server
  profile): the same, to /data/google_token.json, and that token then wins
  over GOOGLE_TOKEN_JSON (google_auth.owner_environ), which keeps working
  until a connect and again after Disconnect (revoked at Google, the file
  deleted, the tools rebuilt on the environment's token). It signs in with
  [calendar] credentials if that is a Web client, else the family's Web
  client (GOOGLE_WEB_CLIENT_JSON), so the saved token carries that client's
  id and secret. Refreshed in memory only (save_token stays off).
- A family member's on a shared server (family.py, [family] google): they
  sign in to *their own* Google from their own phone; the token goes to
  `<family dir>/<id>/google_token.json` and the tools join only their own
  conversations (Family.save_google). Kyle's token is never used for them.
  A beta tester (family.py tier "beta") signs in through the public client
  (GOOGLE_PUBLIC_CLIENT_JSON / [family] public_google_client) and is asked
  for Calendar only; without that client their Google stays off (never the
  family client: its consent screen lists Gmail and Drive).

  GET  /api/google/status      {"enabled", "connected", "disconnect",
                               "gmail", "note", "needs_reconnect",
                               "missing", "server_owner"}: the caller's
                               own account
                               (gmail false: no mail wording in the app;
                               note: why it is off, or null;
                               needs_reconnect: Google refused the saved
                               sign-in, google_auth.needs_reconnect;
                               missing: scopes asked for that the saved
                               token lacks, e.g. Drive's, Reconnect adds
                               them;
                               server_owner: the cloud owner's, no
                               first-run guide)
  POST /api/google/connect     {"url": Google's consent page}
  POST /api/google/disconnect  a family member's token and tools deleted, or
                               the cloud owner's connected one
  GET  /google/callback        ?state&code from Google -> token file, 303 to /

The session cookie is SameSite=Strict, so the browser doesn't send it on
Google's redirect back: the callback is authenticated by the OAuth `state`
and a binding cookie. The state is random, made only for a signed-in
session, kept in memory (at most MAX_PENDING per person and
MAX_PENDING_TOTAL in all, for TTL seconds), usable once, and remembers who
started it (the owner or which family member, and their session). Starting
also sets `hc_google`, a short-lived SameSite=Lax cookie (sent on Google's
top-level redirect, only to the callback path) holding a nonce whose hash
is kept with the state. So a callback counts only in the browser that
started it, while that session is still signed in as the same person: a
consent link sent to someone else can't attach their Google to the
sender's account. Anything else gets a 400 and writes nothing. PKCE (the
code verifier stays on the server) means a stolen code is useless.

Needs a "Web application" OAuth client whose authorized redirect URI is
<origin>/google/callback (a Desktop client only allows localhost): the
owner's from [calendar] credentials / GOOGLE_CLIENT_JSON (with owner_google,
the family's if that one isn't a Web client) with [webapp] public_origin,
the family's from GOOGLE_WEB_CLIENT_JSON / [family] google_client with the
server's own address (family.own_origin).
"""

# No `from __future__ import annotations` here: FastAPI must see the real
# Request class in the route signatures (it is imported inside register()).
import hashlib
import hmac
import logging
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from kwil import i18n
from kwil.tools.google_auth import (OWNER, _relax_scope_check, clear_reconnect,
                                    client_config, granted_scopes, load_token_info,
                                    needs_reconnect, owner_environ,
                                    sign_in_scopes, token_info, token_json,
                                    web_client_config, write_private)

log = logging.getLogger(__name__)

CALLBACK = "/google/callback"
BIND_COOKIE = "hc_google"
MAX_PENDING = 4         # per person (the owner, or one family member)
MAX_PENDING_TOTAL = 32
TTL = 600  # seconds to finish signing in


def _digest(nonce: str) -> str:
    return hashlib.sha256(nonce.encode()).hexdigest()


@dataclass
class Pending:
    """A sign-in waiting for Google's redirect."""
    verifier: str            # PKCE
    created: float
    user: str | None         # None: the owner; else a family member's id
    sid: str | None          # the session that started it
    nonce_hash: str          # of the hc_google cookie set in that browser


class GoogleConnect:
    """The sign-in flow, without HTTP. `tools` is the web app's shared
    ToolRegistry: once the owner signs in, Calendar, Gmail and Drive are added to
    it, and every owner browser's SessionTools sees them (they share its
    dict). `family` (family.Family) handles family members' accounts;
    `owner` False: only family members can connect. `owner_google` ([webapp]
    owner_google, the cloud server): the owner's connected token wins over
    GOOGLE_TOKEN_JSON, and the owner can disconnect it here."""

    def __init__(self, config: dict, tools, clock=time.monotonic, family=None,
                 owner: bool = True):
        self.config = config
        self.tools = tools
        self.clock = clock
        self.family = family
        self.owner = owner
        self.owner_google = bool(owner and config["webapp"].get("owner_google"))
        self.pending: OrderedDict[str, Pending] = OrderedDict()
        self.lock = threading.Lock()
        # the owner's token file and the shared tools: a connect and a
        # disconnect never interleave their write/delete and re-register
        self.owner_lock = threading.Lock()
        # Who a session is now (webapp.WebAuth.user_of; webapp.GONE once
        # signed out). Set by register(); None: not checked.
        self.session_user: Callable[[str], object] | None = None

    @property
    def _cal(self) -> dict:
        return self.config["calendar"]

    @property
    def redirect_uri(self) -> str:
        return self._redirect(None)

    def _redirect(self, user: str | None) -> str:
        return self._origin(user).rstrip("/") + CALLBACK

    def _origin(self, user: str | None) -> str:
        """The address Google sends `user` back to: [webapp] public_origin;
        family members and the cloud owner (owner_google) also the
        server's own (APP_ORIGIN, family.own_origin), so the owner can
        Connect wherever a family member can. "" = unknown."""
        if user is None and not self.owner_google:
            return self.config["webapp"]["public_origin"] or ""
        from kwil.family import own_origin

        return own_origin(self.config)

    def _client(self, user: str | None = None) -> dict | None:
        if user is not None:  # theirs: the family or the public client by tier
            return self.family.google_client(user) if self.family is not None else None
        try:
            client = client_config(self._cal["credentials"])
        except (OSError, ValueError):
            log.warning("Google client file %s is unreadable", self._cal["credentials"])
            client = None
        if not self.owner_google or (client is not None and "web" in client):
            return client
        # the cloud server: GOOGLE_CLIENT_JSON is usually the PC's Desktop
        # client, which Google won't send back to an https address; use the
        # family's Web client (the same Google Cloud project)
        if self.family is not None:
            return self.family.google_client(None)
        try:
            client = web_client_config(self.config["family"]["google_client"])
        except (OSError, ValueError) as e:  # never the value
            log.warning("The Google Web client is unreadable (%s)", e)
            return None
        return client if client is not None and "web" in client else None

    def enabled(self, user: str | None = None) -> bool:
        """Can `user` (None: the owner) connect a Google account here?"""
        if user is not None:
            return self.family is not None and self.family.google_enabled(user)
        return bool(self.owner and self._origin(None)
                    and self._cal["enabled"] and self._client())

    def connected(self, user: str | None = None) -> bool:
        if user is not None:
            return self.family is not None and self.family.google_connected(user)
        if not self.owner:
            return False
        try:  # on the server: the connected token, else GOOGLE_TOKEN_JSON
            return load_token_info(self._cal["token"], owner_environ(self.config)) is not None
        except (OSError, ValueError, AttributeError, TypeError):
            return False

    def needs_reconnect(self, user: str | None = None) -> bool:
        """Did Google refuse `user`'s saved sign-in (revoked, or expired)
        since this server started (google_auth.needs_reconnect)? Only while
        a token is saved: without one the app offers Connect anyway. Known
        from the last refresh the tools made; no call to Google here. The
        token in use counts: after the owner's Disconnect that is
        GOOGLE_TOKEN_JSON again, whose refusal was kept."""
        if not self.connected(user):
            return False
        if user is not None:  # theirs: always their token file
            return needs_reconnect(user, "file")
        try:
            _, from_env = token_info(self._cal["token"], self._cal["credentials"],
                                     owner_environ(self.config))
        except (OSError, ValueError, AttributeError, TypeError):
            return False
        return needs_reconnect(OWNER, "env" if from_env else "file")

    def owner_file(self) -> bool:
        """Has the cloud owner a connected token file to disconnect?"""
        return self.owner_google and Path(self._cal["token"]).is_file()

    def gmail(self, user: str | None = None) -> bool:
        """Does `user`'s sign-in ask for Gmail? Never a beta tester's."""
        if user is not None:
            return self.family is not None and self.family.gmail_allowed(user)
        return bool(self.config["gmail"]["enabled"])

    def drive(self, user: str | None = None) -> bool:
        """Does `user`'s sign-in ask for Drive (read-only)? Never a beta
        tester's (their consent screen is Calendar only)."""
        if user is not None:
            return self.family is not None and self.family.drive_allowed(user)
        return bool(self.config["drive"]["enabled"])

    def scopes(self, user: str | None = None) -> list[str]:
        """What `user`'s sign-in asks Google for."""
        return sign_in_scopes(self.gmail(user), self.drive(user))

    def missing(self, user: str | None = None) -> list[str]:
        """Scopes `user`'s sign-in asks for that their saved token lacks (a
        token from before Drive, or a box left unticked): Reconnect adds
        them. [] when not connected (Connect asks for everything anyway)."""
        if not self.connected(user):
            return []
        try:
            if user is not None:  # their file only, never GOOGLE_TOKEN_JSON
                info = load_token_info(self.family.google_token(user), environ={})
            else:
                info = load_token_info(self._cal["token"], owner_environ(self.config))
        except (OSError, ValueError, AttributeError, TypeError):
            return []
        have = granted_scopes(info)
        return [s for s in self.scopes(user) if s not in have]

    def note(self, user: str | None) -> str | None:
        """Why `user` can't connect, when the app should say so."""
        if user is None or self.family is None or self.enabled(user) \
                or self.family.tier(user) != "beta":
            return None
        return i18n.t("google_beta_off", self.language_of(user))

    def _flow(self, user: str | None = None, **kwargs):
        from google_auth_oauthlib.flow import Flow

        return Flow.from_client_config(
            self._client(user), self.scopes(user),
            redirect_uri=self._redirect(user), **kwargs)

    def start(self, user: str | None = None, sid: str | None = None) -> tuple[str, str]:
        """(Google's consent page URL with a PKCE challenge, the nonce for
        this browser's hc_google cookie)."""
        flow = self._flow(user)
        url, state = flow.authorization_url(access_type="offline", prompt="consent")
        nonce = secrets.token_urlsafe(32)
        with self.lock:
            self._prune()
            theirs = [s for s, p in self.pending.items() if p.user == user]
            while len(theirs) >= MAX_PENDING:
                del self.pending[theirs.pop(0)]
            self.pending[state] = Pending(flow.code_verifier, self.clock(), user,
                                          sid, _digest(nonce))
            while len(self.pending) > MAX_PENDING_TOTAL:
                self.pending.popitem(last=False)
        log.info("Google connect started for %s (%d pending)",
                 user or "the owner", len(self.pending))
        return url, nonce

    def _prune(self) -> None:
        now = self.clock()
        for state in [s for s, p in self.pending.items() if now - p.created > TTL]:
            del self.pending[state]

    def take(self, state: str, nonce: str | None) -> Pending | None:
        """The pending sign-in of `state`, used up. None if unknown,
        expired, finished in another browser, or its session has signed
        out (or its person was removed) meanwhile."""
        with self.lock:
            self._prune()
            entry = self.pending.pop(state, None) if state else None
        if entry is None:
            log.warning("Google callback with an unknown or used state")
            return None
        if not nonce or not hmac.compare_digest(entry.nonce_hash, _digest(nonce)):
            log.warning("Google callback from another browser than the one "
                        "that started it")
            return None
        if self.session_user is not None and entry.sid is not None \
                and self.session_user(entry.sid) != entry.user:
            log.warning("Google callback for a session that has signed out")
            return None
        if entry.user is not None and (self.family is None
                                       or self.family.get(entry.user) is None):
            log.warning("Google callback for someone no longer listed")
            return None
        return entry

    def finish(self, state: str, code: str, nonce: str | None = None
               ) -> tuple[bool, str | None]:
        """Exchange the code for a token, save it, add the tools. Returns
        (done, whose: None = the owner's); nothing is written unless done."""
        entry = self.take(state, nonce)
        if entry is None or not code:
            return False, entry.user if entry is not None else None
        _relax_scope_check()
        flow = self._flow(entry.user, state=state, code_verifier=entry.verifier)
        try:
            flow.fetch_token(code=code)
        except Exception as e:  # e.g. invalid_grant; the message has no secrets
            log.warning("Google token exchange failed (%s)", type(e).__name__)
            return False, entry.user
        text = token_json(flow.credentials)
        if entry.user is not None:  # theirs, in their own folder
            return self.family.save_google(entry.user, text), entry.user
        token = Path(self._cal["token"])
        with self.owner_lock:
            token.parent.mkdir(parents=True, exist_ok=True)
            write_private(token, text)
            # the new file replaces a refused one; GOOGLE_TOKEN_JSON's
            # refusal stays (it is used again after a Disconnect)
            clear_reconnect(OWNER, "file")
            self._register_tools()
        log.info("Google account connected; token saved to %s", token)
        return True, None

    def disconnect(self, user: str | None) -> bool:
        """A family member's token deleted and their Google tools removed;
        `user` None: the cloud owner's connected token (disconnect_owner)."""
        if user is None:
            return self.disconnect_owner()
        return self.family is not None and self.family.disconnect_google(user)

    def disconnect_owner(self) -> bool:
        """The cloud owner's Disconnect ([webapp] owner_google): revoke the
        grant at Google (best effort, as a family member's), delete the
        token file, and rebuild Calendar, Gmail and Drive on GOOGLE_TOKEN_JSON, or
        none without it. False if there was no file, or a new sign-in
        replaced it meanwhile (that one is kept, with its tools)."""
        if not self.owner_file():
            return False
        from kwil.account import revoke_google_token_file
        from kwil.family import _unlink

        token = Path(self._cal["token"])
        with self.owner_lock:
            before = _read_bytes(token)
        if before is None:
            return False
        # outside the lock: the network call (up to its timeout) mustn't
        # hold up a sign-in
        revoke_google_token_file(token, label="Google", who="the owner")
        with self.owner_lock:
            if _read_bytes(token) != before:
                # a Connect finished during the revoke: keep its token
                log.info("Google disconnect skipped: the owner connected again meanwhile")
                return False
            _unlink(token)
            clear_reconnect(OWNER, "file")  # gone; GOOGLE_TOKEN_JSON's stays
            self._register_tools()
        log.info("Google account disconnected (the owner's)")
        return True

    def _register_tools(self) -> None:
        """Calendar, Gmail and Drive on the owner's current token. Every
        SessionTools shares this registry's dict, so it is updated in place,
        adding before removing (a running turn never sees it empty), as
        family._refresh_tools does."""
        from kwil.cli import register_google
        from kwil.tools import ToolRegistry

        fresh = ToolRegistry()
        register_google(fresh, self.config, output_fn=lambda m: log.info("%s", m))
        self.tools.tools.update(fresh.tools)
        gone = sorted(_google_tool_names() & set(self.tools.tools) - set(fresh.tools))
        for name in gone:
            self.tools.tools.pop(name, None)
        log.info("Google tools added: %s", ", ".join(sorted(fresh.tools)) or "none")
        if gone:
            log.info("Google tools removed: %s", ", ".join(gone))

    def language_of(self, user: str | None) -> str | None:
        """The language for a page shown to `user` (None: the server's)."""
        person = self.family.get(user) if user and self.family is not None else None
        return person.get("language") if person else None


def _read_bytes(path: Path) -> bytes | None:
    """The file's contents, or None if it is gone or unreadable."""
    try:
        return path.read_bytes()
    except OSError:
        return None


def _google_tool_names() -> set[str]:
    """Every Calendar, Gmail and Drive tool name (built offline: no service yet)."""
    from kwil.tools.calendar import GoogleCalendar
    from kwil.tools.drive import DriveFiles
    from kwil.tools.gmail import GmailInbox

    return {t.name for t in GoogleCalendar(extras=True).tools() + GmailInbox().tools()
            + DriveFiles().tools()}


def from_config(config: dict, assistant) -> "GoogleConnect | None":
    """The web app's GoogleConnect: when the owner's button is on
    ([webapp] google_connect, or owner_google on the cloud server) or family
    members may connect theirs."""
    family = getattr(assistant, "family", None)
    owner = bool(config["webapp"]["google_connect"] or config["webapp"]["owner_google"])
    if not (owner or (family is not None and config["family"]["google"])):
        return None
    return GoogleConnect(config, assistant.tools, family=family, owner=owner)


def register(app, connect: GoogleConnect, session_of, error, web=None) -> None:
    """Add the routes to a webapp.create_app() app. `web` (webapp.WebAuth)
    lets the callback check that the session that started is still signed
    in as the same person."""
    from fastapi import Request
    from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
    from starlette.concurrency import run_in_threadpool

    if web is not None:
        from kwil.webapp import GONE

        connect.session_user = (lambda sid: web.user_of(sid)
                                if web.signed_in(sid) else GONE)

    def mine(request: Request):
        """(session, its account: None = the owner's), or an error: a
        family member's session sees only its own; with the owner's button
        off the owner has nothing here (404, as before)."""
        s = session_of(request)
        if s is None:
            return None, error("not signed in", 401)
        if s.user is None and not connect.owner:
            return None, error("not found", 404)
        return s, None

    @app.get("/api/google/status")
    def google_status(request: Request):
        s, err = mine(request)
        if err is not None:
            return err
        server_owner = s.user is None and connect.owner_google
        return {"enabled": connect.enabled(s.user), "connected": connect.connected(s.user),
                # a family member can disconnect here, and the cloud owner
                # a token connected here (GOOGLE_TOKEN_JSON, or a PC's
                # token.json, is managed where it was made)
                "disconnect": s.user is not None or connect.owner_file(),
                "gmail": connect.gmail(s.user), "note": connect.note(s.user),
                # Google refused the saved sign-in: the app says "expired,
                # reconnect" instead of "connected"
                "needs_reconnect": connect.needs_reconnect(s.user),
                # scopes the saved token lacks (e.g. Drive, on a token from
                # before it): the app says "Reconnect to add Google Drive"
                "missing": connect.missing(s.user),
                # the cloud owner: no first-run guide (it's for invitees)
                "server_owner": server_owner}

    @app.post("/api/google/connect")
    async def google_connect(request: Request):
        s, err = mine(request)
        if err is not None:
            return err
        if not connect.enabled(s.user):
            return error(connect.note(s.user) or "Google connect is not set up", 503)
        url, nonce = await run_in_threadpool(connect.start, s.user, s.sid)
        response = JSONResponse({"url": url}, headers={"Cache-Control": "no-store"})
        # Lax: sent on Google's top-level redirect back (Strict isn't);
        # only to the callback, and only until the sign-in expires
        response.set_cookie(BIND_COOKIE, nonce, max_age=TTL, httponly=True,
                            secure=True, samesite="lax", path=CALLBACK)
        return response

    @app.post("/api/google/disconnect")
    async def google_disconnect(request: Request):
        s, err = mine(request)
        if err is not None:
            return err
        if s.user is None and not connect.owner_file():
            # the owner's token is managed where it was made (the PC, or
            # GOOGLE_TOKEN_JSON); only one connected here can go here
            return error("not found", 404)
        await run_in_threadpool(connect.disconnect, s.user)
        return {"ok": True, "connected": False}

    @app.get(CALLBACK)
    async def google_callback(request: Request):
        q = request.query_params
        nonce = request.cookies.get(BIND_COOKIE)
        if q.get("error"):
            # e.g. access_denied; still use up the state
            log.info("Google sign-in not finished: %r", q.get("error")[:40])
            entry = connect.take(q.get("state", ""), nonce)
            ok, user = False, entry.user if entry is not None else None
        else:
            ok, user = await run_in_threadpool(connect.finish, q.get("state", ""),
                                               q.get("code", ""), nonce)
        headers = {"Cache-Control": "no-store"}
        if not ok:
            response = PlainTextResponse(
                i18n.t("google_failed", connect.language_of(user)), 400,
                headers=headers)
        else:
            response = RedirectResponse("/", 303, headers=headers)
        response.delete_cookie(BIND_COOKIE, path=CALLBACK, secure=True,
                               httponly=True, samesite="lax")
        return response
