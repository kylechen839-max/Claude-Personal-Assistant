"""Connect an invited person's own Notion from the phone app (public OAuth).

A family member or beta tester (family.py, either tier) taps "Connect
Notion" in Settings, picks the pages to share on Notion's consent page, and
comes back here. Their workspace's Assignments database then gives their
own conversations exactly two tools, on their own token:
`list_assignments` and `update_assignment_status` (Family._notion_tools).
Never Kyle's NOTION_TOKEN, never his lecture recorder, tutor or digests.

  GET  /api/notion/status      {"enabled", "connected", "workspace",
                               "database"}: the caller's own (database null:
                               none, or more than one, Assignments database
                               was shared)
  POST /api/notion/connect     {"url": Notion's consent page}
  POST /api/notion/disconnect  revoke at Notion, delete the token, drop the tools
  GET  /notion/callback        ?state&code from Notion -> token saved, 303 to /

The owner (WEB_TOKEN) gets 404 from all of them: Kyle's Notion is
NOTION_TOKEN on his own machines.

The callback is authenticated exactly like google_web.py's: the session
cookie is SameSite=Strict, so it isn't sent on Notion's redirect back; the
OAuth `state` is random, made only for a signed-in session, kept in memory
(at most MAX_PENDING per person and MAX_PENDING_TOTAL in all, for TTL
seconds), usable once, and bound to the browser that started it by the
`hc_notion` cookie (SameSite=Lax, only sent to the callback path) whose
nonce's hash is kept with the state. It counts only while that session is
still signed in as the same, still listed, person. Anything else is a 400
and writes nothing. Notion documents no PKCE: the client secret never
leaves the server, and a code only works with this redirect URI.

The token answer (access_token, refresh_token, bot_id, workspace_*) is
stored with userkeys.seal under NOTION_LABEL, bound to the person's id, in
`<dir>/<id>/notion.enc` (google_auth.write_private). Notion's `owner.user`
(it carries their email) is never stored. Without a usable USER_DATA_KEY
nothing is exchanged or stored (503 at connect). Codes, tokens, emails,
workspace and database titles are never logged or put in a URL; logs say
ids, counts and error type names only. The SDK's own logger is kept quiet:
its warnings can quote a request body (a filter holding a title).

Finding the database: one search for data sources (page_size SEARCH_PAGE,
one page), kept only if exactly one has the Assignments schema (Assignment
title, Status status with Not started / In progress / Done, Due date),
preferring the copy of the integration's template when Notion says one was
duplicated. Otherwise the status says to share it and reconnect; there is
no picker.

Needs a public Notion integration whose redirect URI is
<own origin>/notion/callback (family.own_origin: [webapp] public_origin /
PUBLIC_ORIGIN), with NOTION_OAUTH_CLIENT_ID and NOTION_OAUTH_CLIENT_SECRET
in the server's environment and [family] notion on.
"""

# No `from __future__ import annotations` here: FastAPI must see the real
# Request class in the route signatures (it is imported inside register()).
import hashlib
import hmac
import logging
import os
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlencode

from assistant import i18n

log = logging.getLogger(__name__)
# Handed to notion_client: its default adds a console handler per Client
# (one more on every refresh) and its warnings can quote request bodies.
_SDK_LOG = logging.getLogger(__name__ + ".sdk")

CALLBACK = "/notion/callback"
BIND_COOKIE = "hc_notion"
MAX_PENDING = 4          # per person
MAX_PENDING_TOTAL = 32
TTL = 600                # seconds to finish signing in
AUTHORIZE_URL = "https://api.notion.com/v1/oauth/authorize"
CLIENT_ID_ENV = "NOTION_OAUTH_CLIENT_ID"
CLIENT_SECRET_ENV = "NOTION_OAUTH_CLIENT_SECRET"
TIMEOUT_MS = 10_000      # per request: a voice turn must not hang on Notion
# The SDK's own 429/5xx retries (3.1's default: 2 retries, honouring a
# Retry-After of up to 60 s) could hold a voice turn, a disconnect or a
# "Delete my account" for minutes: one retry, at most 2 s apart.
MAX_RETRIES = 1
MAX_RETRY_DELAY_MS = 2_000
SEARCH_PAGE = 100        # data sources looked at when connecting (one page)
MAX_TITLE = 100          # characters of a database title kept for the status
# Tests set an httpx.MockTransport here; None: the real network.
TRANSPORT = None


def _digest(nonce: str) -> str:
    return hashlib.sha256(nonce.encode()).hexdigest()


def _plain(rich) -> str:
    return "".join(t.get("plain_text", "") for t in rich or [] if isinstance(t, dict))


def _norm_id(value) -> str:
    return str(value or "").replace("-", "").lower()


def oauth_client(environ=None) -> tuple[str, str] | None:
    """(client id, client secret) of the public integration, or None."""
    env = os.environ if environ is None else environ
    cid, secret = env.get(CLIENT_ID_ENV, "").strip(), env.get(CLIENT_SECRET_ENV, "").strip()
    return (cid, secret) if cid and secret else None


def api(token: str | None = None):
    """A notion_client.Client on `token` alone (None: no Authorization
    header, for the OAuth calls, which send Basic auth themselves). Never
    reads NOTION_TOKEN (Kyle's)."""
    import httpx
    from notion_client import Client

    http = httpx.Client(transport=TRANSPORT) if TRANSPORT is not None else None
    extra = {}
    try:  # 3.1+; 3.0 had no retries of its own
        from notion_client import RetryOptions
    except ImportError:
        pass
    else:
        extra["retry"] = RetryOptions(max_retries=MAX_RETRIES,
                                      max_retry_delay_ms=MAX_RETRY_DELAY_MS)
    return Client(client=http, auth=token or None, timeout_ms=TIMEOUT_MS,
                  logger=_SDK_LOG, log_level=logging.ERROR, **extra)


def _oauth():
    found = oauth_client()
    if found is None:
        raise RuntimeError("Notion OAuth isn't set up")
    return found


def exchange(code: str, redirect_uri: str) -> dict:
    """Trade an authorization code for the token answer. Raises on failure."""
    cid, secret = _oauth()
    return api().oauth.token(client_id=cid, client_secret=secret,
                             grant_type="authorization_code", code=code,
                             redirect_uri=redirect_uri)


def refresh_grant(refresh_token: str) -> dict:
    """A new access token (and maybe a new refresh token). Raises on failure."""
    cid, secret = _oauth()
    return api().oauth.token(client_id=cid, client_secret=secret,
                             grant_type="refresh_token", refresh_token=refresh_token)


def revoke(token: str) -> None:
    """Tell Notion to revoke a token (body, never the URL). Raises on failure."""
    cid, secret = _oauth()
    api().oauth.revoke(client_id=cid, client_secret=secret, token=token)


def revoke_quietly(token: str, uid: str) -> bool:
    """revoke(), best effort: never raises, never logs the token."""
    try:
        revoke(token)
    except Exception as e:  # noqa: BLE001 - disconnecting/deleting goes ahead
        log.warning("Notion: revoking %s's grant failed (%s)", uid, type(e).__name__)
        return False
    log.info("Notion: revoked %s's grant", uid)
    return True


def record_of(answer) -> dict:
    """What notion.enc keeps of Notion's token answer: never `owner` (their
    email) or `duplicated_template_id`. ValueError without an access token."""
    if not isinstance(answer, dict) or not isinstance(answer.get("access_token"), str) \
            or not answer["access_token"]:
        raise ValueError("no access token in Notion's answer")
    keep = ("access_token", "refresh_token", "bot_id", "workspace_id", "workspace_name")
    return {k: answer.get(k) if isinstance(answer.get(k), str) else None for k in keep}


def is_assignments(source) -> bool:
    """Has this data source the Assignments schema the tools use?"""
    from assistant.tools.notion import STATUSES

    if not isinstance(source, dict) or source.get("object") != "data_source":
        return False
    parent = source.get("parent") or {}
    if parent.get("type") != "database_id" or not parent.get("database_id"):
        return False
    props = source.get("properties") or {}
    kind = {name: (props.get(name) or {}).get("type") for name in ("Assignment", "Status", "Due")}
    if kind != {"Assignment": "title", "Status": "status", "Due": "date"}:
        return False
    options = ((props["Status"].get("status") or {}).get("options")) or []
    return set(STATUSES) <= {o.get("name") for o in options if isinstance(o, dict)}


def find_data_source(client, template_id: str | None = None) -> dict | None:
    """{database_id, data_source_id, database_title} of the one shared
    Assignments data source, or None (none, or several: no picker)."""
    answer = client.search(filter={"property": "object", "value": "data_source"},
                           page_size=SEARCH_PAGE)
    results = (answer.get("results") or [])[:SEARCH_PAGE] if isinstance(answer, dict) else []
    found = [s for s in results if is_assignments(s)]
    if template_id and len(found) > 1:  # the copy of the integration's template
        ours = [s for s in found if _norm_id((s.get("database_parent") or {}).get("page_id"))
                == _norm_id(template_id)]
        found = ours or found
    log.info("Notion: %d data source(s) shared, %d with the Assignments schema",
             len(results), len(found))
    if len(found) != 1:
        return None
    source = found[0]
    return {"database_id": source["parent"]["database_id"], "data_source_id": source["id"],
            "database_title": (_plain(source.get("title")) or "Untitled")[:MAX_TITLE]}


@dataclass
class Pending:
    """A sign-in waiting for Notion's redirect."""
    created: float
    user: str                # the person's id
    sid: str | None          # the session that started it
    nonce_hash: str          # of the hc_notion cookie set in that browser


class NotionConnect:
    """The sign-in flow, without HTTP. `family` (family.Family) stores the
    token and gives the person their tools."""

    def __init__(self, family, clock=time.monotonic):
        self.family = family
        self.clock = clock
        self.pending: OrderedDict[str, Pending] = OrderedDict()
        self.lock = threading.Lock()
        # Who a session is now (webapp.WebAuth.user_of; webapp.GONE once
        # signed out). Set by register(); None: not checked.
        self.session_user: Callable[[str], object] | None = None

    @property
    def redirect_uri(self) -> str:
        from assistant.family import own_origin

        return own_origin(self.family.config).rstrip("/") + CALLBACK

    def enabled(self, user: str) -> bool:
        return self.family.notion_enabled(user)

    def status(self, user: str) -> dict:
        return {"enabled": self.enabled(user), **self.family.notion_status(user)}

    def start(self, user: str, sid: str | None = None) -> tuple[str, str]:
        """(Notion's consent page URL, the nonce for this browser's
        hc_notion cookie)."""
        cid, _ = _oauth()
        state = secrets.token_urlsafe(32)
        url = AUTHORIZE_URL + "?" + urlencode({
            "client_id": cid, "response_type": "code", "owner": "user",
            "redirect_uri": self.redirect_uri, "state": state})
        nonce = secrets.token_urlsafe(32)
        with self.lock:
            self._prune()
            theirs = [s for s, p in self.pending.items() if p.user == user]
            while len(theirs) >= MAX_PENDING:
                del self.pending[theirs.pop(0)]
            self.pending[state] = Pending(self.clock(), user, sid, _digest(nonce))
            while len(self.pending) > MAX_PENDING_TOTAL:
                self.pending.popitem(last=False)
            count = len(self.pending)
        log.info("Notion connect started for %s (%d pending)", user, count)
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
            log.warning("Notion callback with an unknown or used state")
            return None
        if not nonce or not hmac.compare_digest(entry.nonce_hash, _digest(nonce)):
            log.warning("Notion callback from another browser than the one "
                        "that started it")
            return None
        if self.session_user is not None and entry.sid is not None \
                and self.session_user(entry.sid) != entry.user:
            log.warning("Notion callback for a session that has signed out")
            return None
        if self.family.get(entry.user) is None:
            log.warning("Notion callback for someone no longer listed")
            return None
        return entry

    def finish(self, state: str, code: str, nonce: str | None = None
               ) -> tuple[bool, str | None]:
        """Exchange the code, find their Assignments database, save the
        token, add the tools. Returns (done, whose); nothing is written
        unless done."""
        from assistant import userkeys

        entry = self.take(state, nonce)
        if entry is None or not code:
            return False, entry.user if entry is not None else None
        uid = entry.user
        if userkeys.secret_problem(self.family.keys.environ) is not None:
            log.warning("Notion: can't store %s's sign-in (%s isn't usable)",
                        uid, userkeys.ENV)
            return False, uid
        try:
            answer = exchange(code, self.redirect_uri)
            record = record_of(answer)
        except Exception as e:  # e.g. invalid_grant; never the message
            log.warning("Notion token exchange failed for %s (%s)", uid, type(e).__name__)
            return False, uid
        try:
            found = find_data_source(api(record["access_token"]),
                                     answer.get("duplicated_template_id"))
        except Exception as e:  # connected anyway; the status says what's missing
            log.warning("Notion: finding %s's Assignments database failed (%s)",
                        uid, type(e).__name__)
            found = None
        record.update(found or {"database_id": None, "data_source_id": None,
                                "database_title": None})
        try:  # a reconnect: the grant it replaces is revoked below
            saved, old = self.family.replace_notion(uid, record)
        except (userkeys.KeyProblem, OSError) as e:
            log.error("Notion: saving %s's sign-in failed (%s)", uid, type(e).__name__)
            saved, old = False, None
        if not saved:  # removed meanwhile, or not stored: don't leave a grant behind
            revoke_quietly(record["access_token"], uid)
            return False, uid
        if old is not None:  # never left live at Notion once replaced
            revoke_quietly(old, uid)
        log.info("Notion: %s connected workspace %s (%s)", uid, record["workspace_id"],
                 "Assignments database found" if found else "no single Assignments database")
        return True, uid

    def disconnect(self, user: str) -> bool:
        return self.family.disconnect_notion(user)

    def language_of(self, user: str | None) -> str | None:
        person = self.family.get(user) if user else None
        return person.get("language") if person else None


def register(app, family, web, error) -> None:
    """Add the routes to a webapp.create_app() app (shaped like
    userkeys_web.register). With [family] off the API routes are 404 and
    there is no callback."""
    from fastapi import Depends, Request
    from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
    from starlette.concurrency import run_in_threadpool

    from assistant import userkeys, webapp

    connect = NotionConnect(family) if family is not None else None
    app.state.notion = connect  # tests
    if connect is not None:
        connect.session_user = (lambda sid: web.user_of(sid)
                                if web.signed_in(sid) else webapp.GONE)

    def person(session):
        """Their id, or None (the owner, or family accounts off): 404."""
        return session.user if connect is not None else None

    @app.get("/api/notion/status")
    def notion_status(session=Depends(webapp.require_session)):
        uid = person(session)
        if uid is None:
            return error("not found", 404)
        return JSONResponse(connect.status(uid), headers={"Cache-Control": "no-store"})

    @app.post("/api/notion/connect")
    async def notion_connect(session=Depends(webapp.require_session)):
        uid = person(session)
        if uid is None:
            return error("not found", 404)
        if not connect.enabled(uid):
            return error("Notion isn't set up on this server", 503)
        if userkeys.secret_problem(family.keys.environ) is not None:  # fail closed
            log.warning("Notion: can't store %s's sign-in (%s isn't usable)",
                        uid, userkeys.ENV)
            return error("this server can't store sign-ins yet (its owner needs "
                         f"to set {userkeys.ENV})", 503)
        url, nonce = await run_in_threadpool(connect.start, uid, session.sid)
        response = JSONResponse({"url": url}, headers={"Cache-Control": "no-store"})
        # Lax: sent on Notion's top-level redirect back (Strict isn't);
        # only to the callback, and only until the sign-in expires
        response.set_cookie(BIND_COOKIE, nonce, max_age=TTL, httponly=True,
                            secure=True, samesite="lax", path=CALLBACK)
        return response

    @app.post("/api/notion/disconnect")
    async def notion_disconnect(session=Depends(webapp.require_session)):
        uid = person(session)
        if uid is None:
            return error("not found", 404)
        try:
            await run_in_threadpool(connect.disconnect, uid)
        except OSError:
            return error("couldn't disconnect Notion; try again", 500)
        return {"ok": True, "connected": False}

    if connect is None:  # family accounts off: no public callback at all
        return

    @app.get(CALLBACK)
    async def notion_callback(request: Request):
        q = request.query_params
        nonce = request.cookies.get(BIND_COOKIE)
        if q.get("error"):  # e.g. access_denied; still use up the state
            log.info("Notion sign-in not finished (%d-character error code)",
                     len(q.get("error")))
            entry = connect.take(q.get("state", ""), nonce)
            ok, user = False, entry.user if entry is not None else None
        else:
            ok, user = await run_in_threadpool(connect.finish, q.get("state", ""),
                                               q.get("code", ""), nonce)
        headers = {"Cache-Control": "no-store"}
        if not ok:
            response = PlainTextResponse(
                i18n.t("notion_failed", connect.language_of(user)), 400, headers=headers)
        else:
            response = RedirectResponse("/", 303, headers=headers)
        response.delete_cookie(BIND_COOKIE, path=CALLBACK, secure=True,
                               httponly=True, samesite="lax")
        return response
