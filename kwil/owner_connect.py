"""The owner's own services, connected by signing in from the phone app.

Kyle opens Settings -> "Connected services" on the cloud app and signs in to
a service there (a same-tab redirect, like "Connect Notion" for invited
people), instead of pasting its secret into Render's environment. This is
the shared part; each service is a Connector below:

- "notion": his Notion through the public integration notion_web.py already
  uses for invited people (NOTION_OAUTH_CLIENT_ID / _SECRET, the same
  redirect URI <origin>/notion/callback: notion_web hands an owner's state
  to this module). It feeds his Assignments tools, lecture notes, team chat
  digests and the dev team inbox, which all ask owner_notion_client().
- "home_assistant": his Home Assistant through its built-in OAuth
  (IndieAuth-style: the client id is this site's address, so nothing is
  registered anywhere). He types its public https address first.

  GET  /api/connect/status              {"services": [...]}  (the owner; 404 for others)
  POST /api/connect/<id>/start          {"url"?} -> {"url": the sign-in page}
  POST /api/connect/<id>/disconnect     revoke, delete, drop the tools
  GET  /notion/callback                 (notion_web.py's route; an owner state comes here)
  GET  /ha/callback                     Home Assistant's redirect back

Precedence: the environment first. With NOTION_TOKEN (or HA_TOKEN) set,
that service is "managed": "env" exactly as before, and Connect is refused
(409) and hidden. Otherwise "app" once signed in here, else "none".

Storage: one file per service in [connect] dir (Render: /data/owner),
sealed with userkeys.seal under a label of its own (LABELS) and the id
"owner", written with google_auth.write_private. Notion: the token answer
as notion_web.record_of keeps it (never Notion's `owner` object). Home
Assistant: its address and the refresh token; access tokens (30 minutes)
live in memory only. Without a usable USER_DATA_KEY nothing is started or
stored (503). Disconnect revokes at the service (best effort; a failure is
a WARNING with the error type), then deletes the file, then removes the
tools from the owner's registry. A sign-in that couldn't be stored is
revoked at once.

The callback is authenticated like notion_web.py's: the session cookie is
SameSite=Strict and isn't sent on the provider's redirect back, so the
OAuth `state` is random, made only for the owner's signed-in session, kept
in memory (at most MAX_PENDING, for TTL seconds), usable once, and bound to
the browser that started it by the `hc_connect` cookie (SameSite=Lax, only
sent to that service's callback path) whose nonce's hash is kept with it.
It counts only while that session is still signed in as the owner.
Anything else is a 400 and writes nothing. Home Assistant gets PKCE (S256;
HA 2026.10 checks it, older versions ignore it); Notion documents none.

Home Assistant's address must pass canvas.public_url: https on port 443, no
user:password@, and every address the name resolves to on the public
internet (never this machine, the LAN or a cloud metadata address); it is
checked again before each token request. So it only works when HA is
reachable from the internet (Nabu Casa, or a tunnel). Plain http is never
accepted, so tools.home_assistant.plain_http_off_lan can't apply.

Logs: ids, service names, counts, HTTP statuses and error type names.
Never a code, token, address typed in, workspace name or provider message.
"""

# No `from __future__ import annotations` here: FastAPI must see the real
# Request class in the route signatures (it is imported inside register()).
import base64
import hashlib
import hmac
import logging
import os
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode, urlsplit

from kwil import i18n, userkeys

log = logging.getLogger(__name__)

OWNER = "owner"                 # the id sealed inside each file
SERVICES = ("notion", "home_assistant")
NAMES = {"notion": "Notion", "home_assistant": "Home Assistant"}
LABELS = {"notion": b"kwil/notion/v1", "home_assistant": b"kwil/home_assistant/v1"}
FILES = {"notion": "notion.enc", "home_assistant": "home_assistant.enc"}
CALLBACKS = {"notion": "/notion/callback", "home_assistant": "/ha/callback"}
ENV_TOKENS = {"notion": "NOTION_TOKEN", "home_assistant": "HA_TOKEN"}
TOOL_NAMES = {"notion": ("list_assignments", "update_assignment_status"),
              "home_assistant": ("ha_list", "ha_state", "ha_control")}
BIND_COOKIE = "hc_connect"
MAX_PENDING = 4                 # sign-ins waiting for a redirect (the owner only)
TTL = 600                       # seconds to finish signing in
MAX_BODY = 2048                 # bytes of a start request
MAX_URL = 300                   # characters of a typed Home Assistant address
HA_TIMEOUT = 10                 # seconds per Home Assistant OAuth request
MAX_ANSWER = 65536              # bytes of a token answer read
HA_SKEW = 60                    # refresh an access token this long before it ends
HA_MAX_LIFETIME = 1800          # never keep an access token longer than HA's 30 minutes
# Tests set an httpx.MockTransport here (Home Assistant's OAuth calls; the
# Notion ones go through notion_web.TRANSPORT); None: the real network.
TRANSPORT = None


class ConnectProblem(Exception):
    """A start/disconnect that can't go ahead; `status` is the HTTP answer.
    The message never holds what was typed or a token."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class NotConnected(RuntimeError):
    """The owner's service has no usable sign-in (never connected,
    disconnected, or the file no longer opens)."""


def _digest(nonce: str) -> str:
    return hashlib.sha256(nonce.encode()).hexdigest()


def _env(environ=None):
    return os.environ if environ is None else environ


# ---------------------------------------------------------------- storage
class Vault:
    """The owner's sealed sign-ins, one file per service in [connect] dir."""

    def __init__(self, folder: "str | Path", environ=None):
        self.dir = Path(folder)
        self.environ = environ

    def problem(self) -> "str | None":
        return userkeys.secret_problem(_env(self.environ))

    def path(self, svc: str) -> Path:
        return self.dir / FILES[svc]

    def stored(self, svc: str) -> bool:
        return self.path(svc).exists()

    def load(self, svc: str) -> "dict | None":
        try:
            raw = self.path(svc).read_bytes()
        except FileNotFoundError:
            return None
        except OSError as e:
            log.warning("Connect: the owner's %s file is unreadable (%s)", svc,
                        type(e).__name__)
            return None
        return userkeys.unseal(LABELS[svc], OWNER, raw, _env(self.environ),
                               what=f"{NAMES[svc]} sign-in", redo="connect it again")

    def save(self, svc: str, record: dict) -> None:
        """Seal and write privately. KeyProblem 503 without USER_DATA_KEY."""
        from kwil.tools.google_auth import write_private

        record = {k: v for k, v in record.items() if k != "uid"}
        sealed = userkeys.seal(LABELS[svc], OWNER, record, _env(self.environ))
        path = self.path(svc)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_private(path, sealed)

    def delete(self, svc: str) -> bool:
        from kwil.family import _unlink

        path = self.path(svc)
        had = path.exists()
        _unlink(path)
        return had


def vault_of(config: dict, environ=None) -> Vault:
    return Vault(config["connect"]["dir"], environ)


# One lock per service: a refresh, a (re)connect's save and a disconnect
# run one at a time, so none brings back a sign-in another one just
# replaced or deleted. (Per service, not per folder: a process has one
# [connect] dir, and a fixed set can't grow.)
_LOCKS = {svc: threading.Lock() for svc in SERVICES}
_LOCKS_GUARD = threading.Lock()


def _lock(vault: Vault, svc: str) -> threading.Lock:
    return _LOCKS[svc]


def service_on(config: dict, svc: str) -> bool:
    c = config["connect"]
    return bool(c["enabled"] and c[svc])


def managed(config: dict, svc: str, environ=None) -> str:
    """"env" (its token is in the environment: today's setup, unchanged),
    "app" (signed in from the phone app), or "none"."""
    if _env(environ).get(ENV_TOKENS[svc]):
        return "env"
    if service_on(config, svc) and vault_of(config, environ).stored(svc):
        return "app"
    return "none"


def _origin(config: dict) -> str:
    from kwil.family import own_origin

    origin = own_origin(config).rstrip("/")
    return origin if origin.startswith("https://") else ""


# ---------------------------------------------------------------- Notion
def owner_notion_token(config: dict, environ=None) -> "str | None":
    """The owner's Notion token: NOTION_TOKEN, else the app sign-in's
    access token, else None. Never logged."""
    env = _env(environ).get("NOTION_TOKEN")
    if env:
        return env
    if not service_on(config, "notion"):
        return None
    record = vault_of(config, environ).load("notion")
    token = (record or {}).get("access_token")
    return token if isinstance(token, str) and token else None


def notion_connectable(config: dict, environ=None) -> bool:
    """Can the owner connect Notion here ([connect] notion, the public
    integration, an https public origin)?"""
    from kwil import notion_web

    return bool(service_on(config, "notion") and notion_web.oauth_client(environ)
                and _origin(config))


def refresh_owner_notion(config: dict, failed: str, environ=None) -> "str | None":
    """After a 401 on access token `failed`: a working access token, or None
    (connect again). One refresh at a time; a caller that waited gets the
    token the other one got. A new token that can't be stored is revoked."""
    from kwil import notion_web

    vault = vault_of(config, environ)
    with _lock(vault, "notion"):
        record = vault.load("notion")
        if record is None:
            return None
        if record.get("access_token") != failed:
            return record.get("access_token")  # refreshed meanwhile
        if not record.get("refresh_token"):
            return None
        try:
            answer = notion_web.refresh_grant(record["refresh_token"])
            access = notion_web.record_of(answer)["access_token"]
        except Exception as e:  # never the message
            log.warning("Connect: refreshing the owner's Notion token failed (%s)",
                        type(e).__name__)
            return None
        rotated = answer.get("refresh_token")
        fresh = {**record, "access_token": access,
                 "refresh_token": rotated if isinstance(rotated, str) and rotated
                 else record["refresh_token"]}
        try:
            vault.save("notion", fresh)
        except (userkeys.KeyProblem, OSError) as e:
            log.warning("Connect: saving the owner's refreshed Notion token failed (%s)",
                        type(e).__name__)
            notion_web.revoke_quietly(access, OWNER)
            return None
    log.info("Connect: refreshed the owner's Notion token")
    return access


def _notion_auth(config: dict, environ=None):
    """An httpx.Auth that sends the owner's current Notion token on every
    request (read each time: a connect, refresh or disconnect applies at
    once) and, for an app sign-in, refreshes it once on a 401."""
    import httpx

    class OwnerNotionAuth(httpx.Auth):
        def auth_flow(self, request):
            env = _env(environ).get("NOTION_TOKEN")
            token = env or owner_notion_token(config, environ)
            if not token:
                raise NotConnected("Notion isn't connected: connect it in the "
                                   "app's settings (Connected services)")
            request.headers["Authorization"] = f"Bearer {token}"
            response = yield request
            if response.status_code == 401 and not env:
                fresh = refresh_owner_notion(config, token, environ)
                if fresh and fresh != token:
                    request.headers["Authorization"] = f"Bearer {fresh}"
                    yield request

    return OwnerNotionAuth()


def owner_notion_client(config: dict, environ=None):
    """A notion_client.Client for the owner, or None (no NOTION_TOKEN and
    Notion can't be connected here). With NOTION_TOKEN: the client every
    caller made before (Client(auth=NOTION_TOKEN)). Otherwise one whose
    token is looked up on every request, so a caller built before the
    owner connects (lecture notes, digests, the dev team inbox) works once
    he does, and stops when he disconnects."""
    from notion_client import Client

    env = _env(environ).get("NOTION_TOKEN")
    if env:
        return Client(auth=env)
    if managed(config, "notion", environ) != "app" and not notion_connectable(config, environ):
        return None
    import httpx

    from kwil import notion_web

    http = httpx.Client(transport=notion_web.TRANSPORT, auth=_notion_auth(config, environ))
    return Client(client=http, logger=notion_web._SDK_LOG, log_level=logging.ERROR)


def owner_assignments(config: dict, environ=None):
    """The owner's NotionAssignments ([notion] database_id), or None. Never
    NotionAssignments(token="") (it would fall back to NOTION_TOKEN)."""
    if not config["notion"]["enabled"]:
        return None
    from kwil.tools.notion import NotionAssignments

    tz = config["assistant"]["timezone"] or None
    db = config["notion"]["database_id"]
    if _env(environ).get("NOTION_TOKEN"):
        return NotionAssignments(db, timezone=tz)  # as before: NOTION_TOKEN
    client = owner_notion_client(config, environ)
    return NotionAssignments(db, client=client, timezone=tz) if client is not None else None


def notion_tools(config: dict, environ=None) -> list:
    """list_assignments and update_assignment_status, if the owner's Notion
    is set (NOTION_TOKEN or an app sign-in). The same tools, flags and
    taint rules as ever."""
    if not config["notion"]["enabled"] or managed(config, "notion", environ) == "none":
        return []
    found = owner_assignments(config, environ)
    return found.tools() if found is not None else []


# ---------------------------------------------------------------- Home Assistant
def ha_origin(url) -> str:
    """https://host of a typed Home Assistant address, checked with
    canvas.public_url; ConnectProblem(400) otherwise. Slow (DNS)."""
    from kwil.canvas import public_url

    if not isinstance(url, str) or not url.strip() or len(url) > MAX_URL:
        raise ConnectProblem("type your Home Assistant's public https address")
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    try:
        parts = urlsplit(public_url(url))
    except ValueError:
        raise ConnectProblem("that isn't a public https address Kwil can reach "
                             "(use Nabu Casa's address or a tunnel)") from None
    if parts.path.strip("/") or parts.query or parts.fragment:
        raise ConnectProblem("type just the address, e.g. https://example.ui.nabu.casa")
    host = parts.hostname
    return f"https://[{host}]" if ":" in host else f"https://{host}"  # (IPv6)


def ha_client_id(config: dict) -> str:
    """This site's address: Home Assistant's IndieAuth client id."""
    return _origin(config) + "/"


class OAuthFailed(RuntimeError):
    """Home Assistant answered a token request with an error: `status` only
    (its answer's text is never kept)."""

    def __init__(self, status: int):
        super().__init__(f"HTTP {status}")
        self.status = status


def ha_post(origin: str, path: str, form: dict) -> dict:
    """POST a form to the owner's Home Assistant (its address checked again
    first). The JSON answer; OAuthFailed for an error status."""
    import httpx

    ha_origin(origin)  # still public (a name can be pointed elsewhere later)
    with httpx.Client(transport=TRANSPORT, timeout=HA_TIMEOUT,
                      follow_redirects=False) as http:
        response = http.post(origin + path, data=form)
    if response.status_code != 200:
        raise OAuthFailed(response.status_code)
    if path == "/auth/revoke":
        return {}
    if len(response.content) > MAX_ANSWER:
        raise ValueError("answer too large")
    answer = response.json()
    if not isinstance(answer, dict) or not isinstance(answer.get("access_token"), str) \
            or not answer["access_token"]:
        raise ValueError("no access token in the answer")
    return answer


def _why(e: Exception) -> str:
    """An error for the log: its type, and the HTTP status of an OAuthFailed."""
    return f"{type(e).__name__} {e.status}" if isinstance(e, OAuthFailed) else type(e).__name__


class HAAuth:
    """Access tokens for the owner's app-connected Home Assistant: made from
    the sealed refresh token, kept in memory only, refreshed shortly before
    they end and once after a 401."""

    def __init__(self, config: dict, environ=None, clock=time.monotonic):
        self.config = config
        self.environ = environ
        self.clock = clock
        self.access: "str | None" = None
        self.expires = 0.0
        self.lock = threading.Lock()

    def prime(self, answer: dict) -> None:
        with self.lock:
            self.access = answer["access_token"]
            self.expires = self.clock() + self._lifetime(answer)

    def clear(self) -> None:
        with self.lock:
            self.access, self.expires = None, 0.0

    @staticmethod
    def _lifetime(answer: dict) -> float:
        try:
            seconds = float(answer.get("expires_in") or HA_MAX_LIFETIME)
        except (TypeError, ValueError):
            seconds = HA_MAX_LIFETIME
        return max(0.0, min(seconds, HA_MAX_LIFETIME))

    def token(self, failed: "str | None" = None) -> str:
        """A current access token. `failed`: the one Home Assistant just
        refused (refresh unless another thread already did). Raises
        HAError("ha_reconnect") without a usable sign-in, "ha_down" when
        Home Assistant can't be reached."""
        from kwil.tools.home_assistant import HAError

        with self.lock:
            if self.access is not None:
                if failed is not None and failed != self.access:
                    return self.access  # another turn refreshed it meanwhile
                if failed is None and self.clock() < self.expires - HA_SKEW:
                    return self.access
            record = vault_of(self.config, self.environ).load("home_assistant")
            if not record or not record.get("refresh_token") or not record.get("url"):
                self.access = None
                raise HAError("ha_reconnect")
            try:
                answer = ha_post(record["url"], "/auth/token", {
                    "grant_type": "refresh_token", "refresh_token": record["refresh_token"],
                    "client_id": ha_client_id(self.config)})
            except Exception as e:  # never the answer's text
                log.warning("Connect: refreshing the owner's Home Assistant token "
                            "failed (%s)", _why(e))
                self.access = None
                # a refused refresh token means signing in again; anything
                # else (unreachable, 5xx): Home Assistant is down
                if isinstance(e, OAuthFailed) and e.status in (400, 401, 403):
                    raise HAError("ha_reconnect") from None
                raise HAError("ha_down", type(e).__name__) from None
            self.access = answer["access_token"]
            self.expires = self.clock() + self._lifetime(answer)
        log.info("Connect: refreshed the owner's Home Assistant access token")
        return answer["access_token"]


# Shared by the startup registry (cli.build_tools) and the web routes, so a
# connect, a refresh and a disconnect all see the same in-memory token.
_HA_AUTHS: dict = {}


def ha_auth(config: dict, environ=None) -> HAAuth:
    key = str(Path(config["connect"]["dir"]).resolve())
    with _LOCKS_GUARD:
        found = _HA_AUTHS.get(key)
        if found is None or found.environ is not environ:
            if len(_HA_AUTHS) > 8:  # tests make many folders; a server has one
                _HA_AUTHS.clear()
            found = _HA_AUTHS[key] = HAAuth(config, environ)
        found.config = config
        return found


def ha_tools(config: dict, environ=None) -> list:
    """ha_list / ha_state / ha_control on the app sign-in (the same tools
    and rules as with HA_TOKEN), or [] if there is none (or HA_TOKEN is set:
    cli.register_home_assistant uses that)."""
    if managed(config, "home_assistant", environ) != "app":
        return []
    record = vault_of(config, environ).load("home_assistant")
    if not record or not record.get("url"):
        return []
    from kwil.tools.home_assistant import HomeAssistant

    ha = config["home_assistant"]
    return HomeAssistant(
        url=record["url"], token_fn=ha_auth(config, environ).token,
        domains=ha["domains"], scripts=ha["scripts"],
        secure_entities=ha["secure_entities"],
        cache_seconds=float(ha["cache_seconds"]), timeout=float(ha["timeout"])).tools()


# ---------------------------------------------------------------- the flow
@dataclass
class Pending:
    """A sign-in waiting for the provider's redirect."""
    created: float
    svc: str
    sid: "str | None"            # the owner session that started it
    nonce_hash: str              # of the hc_connect cookie set in that browser
    verifier: str = ""           # PKCE (Home Assistant)
    url: str = ""                # Home Assistant's checked address


class OwnerConnect:
    """The owner's connectors, without HTTP. `tools` is the web app's
    shared ToolRegistry (every owner browser's SessionTools shares its
    dict): connecting adds a service's tools, disconnecting removes them."""

    def __init__(self, config: dict, tools, environ=None, clock=time.monotonic):
        self.config = config
        self.tools = tools
        self.environ = environ
        self.clock = clock
        self.pending: OrderedDict = OrderedDict()
        self.lock = threading.Lock()
        # Who a session is now (webapp.WebAuth.user_of; webapp.GONE once
        # signed out; None: the owner). Set by register().
        self.session_user: "Callable[[str], object] | None" = None

    @property
    def vault(self) -> Vault:
        return vault_of(self.config, self.environ)

    def services(self) -> list:
        return [s for s in SERVICES if service_on(self.config, s)]

    def available(self, svc: str) -> bool:
        if svc == "notion":
            return notion_connectable(self.config, self.environ)
        return bool(_origin(self.config))

    def status(self) -> dict:
        out = []
        for svc in self.services():
            how = managed(self.config, svc, self.environ)
            problem = None
            if how == "none":
                if not self.available(svc):
                    problem = "setup"
                elif self.vault.problem() is not None:
                    problem = "key"
            detail = None
            if how == "app":
                record = self.vault.load(svc)
                if record is None:
                    problem = "reconnect"
                elif svc == "notion":
                    detail = record.get("workspace_name")
                else:
                    detail = urlsplit(record.get("url") or "").hostname
            out.append({"id": svc, "name": NAMES[svc], "managed": how,
                        "problem": problem, "needs_url": svc == "home_assistant",
                        "detail": detail[:100] if isinstance(detail, str) else None})
        return {"services": out}

    # ----- starting -----
    def start(self, svc: str, sid: "str | None", url=None) -> "tuple[str, str]":
        """(the sign-in page's URL, the nonce for this browser's hc_connect
        cookie). ConnectProblem: 404 an unknown or switched-off service,
        409 set in the environment, 503 not set up / no USER_DATA_KEY,
        400 a bad Home Assistant address."""
        if svc not in self.services():
            raise ConnectProblem("not found", 404)
        if managed(self.config, svc, self.environ) == "env":
            raise ConnectProblem(f"{NAMES[svc]} is set on the server "
                                 f"({ENV_TOKENS[svc]}); remove that to connect here", 409)
        if not self.available(svc):
            raise ConnectProblem(f"{NAMES[svc]} sign-in isn't set up on this server", 503)
        if self.vault.problem() is not None:  # fail closed
            log.warning("Connect: can't store the owner's %s sign-in (%s isn't usable)",
                        svc, userkeys.ENV)
            raise ConnectProblem("this server can't store sign-ins yet (set "
                                 f"{userkeys.ENV})", 503)
        state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        origin = _origin(self.config)
        if svc == "notion":
            from kwil import notion_web

            cid, _ = notion_web.oauth_client(self.environ)
            page = notion_web.AUTHORIZE_URL + "?" + urlencode({
                "client_id": cid, "response_type": "code", "owner": "user",
                "redirect_uri": origin + CALLBACKS[svc], "state": state})
            entry = Pending(self.clock(), svc, sid, _digest(nonce))
        else:
            ha = ha_origin(url)
            verifier = secrets.token_urlsafe(48)
            challenge = base64.urlsafe_b64encode(
                hashlib.sha256(verifier.encode("ascii")).digest()).decode().rstrip("=")
            page = ha + "/auth/authorize?" + urlencode({
                "client_id": origin + "/", "redirect_uri": origin + CALLBACKS[svc],
                "state": state, "code_challenge": challenge,
                "code_challenge_method": "S256"})
            entry = Pending(self.clock(), svc, sid, _digest(nonce), verifier, ha)
        with self.lock:
            self._prune()
            self.pending[state] = entry
            while len(self.pending) > MAX_PENDING:
                self.pending.popitem(last=False)
            count = len(self.pending)
        log.info("Connect: the owner started a %s sign-in (%d pending)", svc, count)
        return page, nonce

    def _prune(self) -> None:
        now = self.clock()
        for state in [s for s, p in self.pending.items() if now - p.created > TTL]:
            del self.pending[state]

    def claims(self, svc: str, state: str) -> bool:
        """Is `state` one of the owner's pending sign-ins for `svc`? (notion_web
        asks, to hand the shared /notion/callback over.)"""
        with self.lock:
            entry = self.pending.get(state) if state else None
        return entry is not None and entry.svc == svc

    def take(self, svc: str, state: str, nonce: "str | None") -> "Pending | None":
        """The pending sign-in of `state`, used up; None if unknown, expired,
        another service's, finished in another browser, or its session is
        no longer the owner's."""
        with self.lock:
            self._prune()
            entry = self.pending.pop(state, None) if state else None
        if entry is None or entry.svc != svc:
            log.warning("Connect: %s callback with an unknown or used state", svc)
            return None
        if not nonce or not hmac.compare_digest(entry.nonce_hash, _digest(nonce)):
            log.warning("Connect: %s callback from another browser than the one "
                        "that started it", svc)
            return None
        if self.session_user is not None and entry.sid is not None \
                and self.session_user(entry.sid) is not None:
            log.warning("Connect: %s callback for a session that has signed out", svc)
            return None
        return entry

    # ----- finishing -----
    def finish(self, svc: str, state: str, code: str, nonce: "str | None" = None) -> bool:
        """Exchange the code, store the sign-in sealed, add the tools. False
        (and nothing stored) on any failure."""
        entry = self.take(svc, state, nonce)
        if entry is None or not code or len(code) > 2048:
            return False
        if self.vault.problem() is not None:
            log.warning("Connect: can't store the owner's %s sign-in (%s isn't usable)",
                        svc, userkeys.ENV)
            return False
        if svc == "notion":
            return self._finish_notion(code)
        return self._finish_ha(entry, code)

    def _finish_notion(self, code: str) -> bool:
        from kwil import notion_web

        try:
            record = notion_web.record_of(notion_web.exchange(
                code, _origin(self.config) + CALLBACKS["notion"]))
        except Exception as e:  # e.g. invalid_grant; never the message
            log.warning("Connect: the owner's Notion token exchange failed (%s)",
                        type(e).__name__)
            return False
        vault = self.vault
        with _lock(vault, "notion"):
            before = vault.load("notion")
            try:
                vault.save("notion", record)
            except (userkeys.KeyProblem, OSError) as e:
                log.error("Connect: saving the owner's Notion sign-in failed (%s)",
                          type(e).__name__)
                notion_web.revoke_quietly(record["access_token"], OWNER)
                return False
        if before is not None and before.get("access_token") != record["access_token"] \
                and not notion_web.same_bot(before, record):
            notion_web.revoke_quietly(before["access_token"], OWNER)  # the grant it replaced
        added = self._add_tools("notion")
        log.info("Connect: the owner connected Notion (workspace %s, %d tools)",
                 record.get("workspace_id"), added)
        return True

    def _finish_ha(self, entry: Pending, code: str) -> bool:
        try:
            answer = ha_post(entry.url, "/auth/token", {
                "grant_type": "authorization_code", "code": code,
                "client_id": ha_client_id(self.config), "code_verifier": entry.verifier})
        except Exception as e:  # never the answer's text
            log.warning("Connect: the owner's Home Assistant token exchange failed "
                        "(%s)", _why(e))
            return False
        refresh = answer.get("refresh_token")
        if not isinstance(refresh, str) or not refresh:
            log.warning("Connect: Home Assistant sent no refresh token")
            return False
        vault = self.vault
        with _lock(vault, "home_assistant"):
            before = vault.load("home_assistant")
            try:
                vault.save("home_assistant", {"url": entry.url, "refresh_token": refresh})
            except (userkeys.KeyProblem, OSError) as e:
                log.error("Connect: saving the owner's Home Assistant sign-in failed (%s)",
                          type(e).__name__)
                self._revoke_ha(entry.url, refresh)
                return False
            ha_auth(self.config, self.environ).prime(answer)
        if before and before.get("refresh_token") and before["refresh_token"] != refresh:
            self._revoke_ha(before.get("url") or "", before["refresh_token"])
        added = self._add_tools("home_assistant")
        log.info("Connect: the owner connected Home Assistant (%d tools)", added)
        return True

    # ----- tools -----
    def _add_tools(self, svc: str) -> int:
        from kwil.tools import ToolRegistry

        fresh = ToolRegistry()
        try:
            fresh.register(*(notion_tools(self.config, self.environ) if svc == "notion"
                             else ha_tools(self.config, self.environ)))
        except Exception as e:  # connected anyway; the next restart registers them
            log.warning("Connect: adding the %s tools failed (%s)", svc, type(e).__name__)
            return 0
        if self.tools is not None:
            self.tools.tools.update(fresh.tools)  # one step: never half-registered
        return len(fresh.tools)

    def _drop_tools(self, svc: str) -> None:
        if self.tools is not None:
            for name in TOOL_NAMES[svc]:
                self.tools.tools.pop(name, None)

    # ----- disconnecting -----
    @staticmethod
    def _revoke_ha(url: str, refresh: str) -> bool:
        try:
            ha_post(url, "/auth/revoke", {"token": refresh})
        except Exception as e:  # noqa: BLE001 - deleting goes ahead
            log.warning("Connect: revoking the owner's Home Assistant sign-in failed (%s)",
                        _why(e))
            return False
        log.info("Connect: revoked the owner's Home Assistant sign-in")
        return True

    def disconnect(self, svc: str) -> bool:
        """Revoke at the service (best effort), delete the file, remove the
        tools. ConnectProblem 404/409 for an unknown or env-managed service.
        False if nothing was stored."""
        if svc not in self.services():
            raise ConnectProblem("not found", 404)
        if managed(self.config, svc, self.environ) == "env":
            raise ConnectProblem(f"{NAMES[svc]} is set on the server "
                                 f"({ENV_TOKENS[svc]}), not here", 409)
        vault = self.vault
        with _lock(vault, svc):
            record = vault.load(svc)
            if record is not None:
                if svc == "notion" and record.get("access_token"):
                    from kwil import notion_web

                    notion_web.revoke_quietly(record["access_token"], OWNER)
                elif svc == "home_assistant" and record.get("refresh_token"):
                    self._revoke_ha(record.get("url") or "", record["refresh_token"])
            had = vault.delete(svc)
            if svc == "home_assistant":
                ha_auth(self.config, self.environ).clear()
            self._drop_tools(svc)
        log.info("Connect: the owner disconnected %s", svc)
        return had


def from_config(config: dict, tools) -> "OwnerConnect | None":
    """The owner's connectors for the web app, or None ([connect] off)."""
    return OwnerConnect(config, tools) if config["connect"]["enabled"] else None


def register_tools(registry, config: dict, output_fn=print, environ=None) -> None:
    """At startup (cli.build_tools): the tools of services the owner
    connected from the app. Env-managed ones are registered as before."""
    for svc, make in (("notion", notion_tools), ("home_assistant", ha_tools)):
        if managed(config, svc, environ) != "app" \
                or (svc == "notion" and not config["notion"]["enabled"]):
            continue
        try:
            found = make(config, environ)
        except Exception as e:  # noqa: BLE001 - one service must not stop startup
            log.warning("Connect: the owner's %s tools are off (%s)", svc, type(e).__name__)
            found = []
        if found:
            registry.register(*found)
        else:
            output_fn(f"({NAMES[svc]} tools off: the app sign-in no longer opens; "
                      "connect it again in Settings)")


# ---------------------------------------------------------------- HTTP
def register(app, connect: "OwnerConnect | None", web, error) -> None:
    """Add the routes to a webapp.create_app() app (after notion_web's, whose
    /notion/callback hands the owner's states here)."""
    from fastapi import Depends, Request
    from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
    from starlette.concurrency import run_in_threadpool

    from kwil import webapp

    app.state.owner_connect = connect
    if connect is None:
        return
    connect.session_user = (lambda sid: web.user_of(sid)
                            if web.signed_in(sid) else webapp.GONE)
    no_store = {"Cache-Control": "no-store"}

    @app.get("/api/connect/status")
    def connect_status(session=Depends(webapp.require_owner)):
        return JSONResponse(connect.status(), headers=no_store)

    @app.post("/api/connect/{svc}/start")
    async def connect_start(svc: str, request: Request,
                            session=Depends(webapp.require_owner)):
        raw = await request.body()
        if len(raw) > MAX_BODY:
            return error("too large", 413)
        import json

        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            return error("bad request", 400)
        url = body.get("url") if isinstance(body, dict) else None
        try:
            page, nonce = await run_in_threadpool(connect.start, svc, session.sid, url)
        except ConnectProblem as e:
            return error(str(e), e.status)
        response = JSONResponse({"url": page}, headers=no_store)
        # Lax: sent on the provider's top-level redirect back (Strict isn't);
        # only to this service's callback, and only until the sign-in expires
        response.set_cookie(BIND_COOKIE, nonce, max_age=TTL, httponly=True,
                            secure=True, samesite="lax", path=CALLBACKS[svc])
        return response

    @app.post("/api/connect/{svc}/disconnect")
    async def connect_disconnect(svc: str, session=Depends(webapp.require_owner)):
        try:
            await run_in_threadpool(connect.disconnect, svc)
        except ConnectProblem as e:
            return error(str(e), e.status)
        except OSError:
            return error("couldn't disconnect; try again", 500)
        return {"ok": True, "managed": managed(connect.config, svc, connect.environ)}

    async def callback(svc: str, request: Request):
        q = request.query_params
        state, nonce = q.get("state", ""), request.cookies.get(BIND_COOKIE)
        if q.get("error"):  # e.g. access_denied; still use up the state
            log.info("Connect: %s sign-in not finished (%d-character error code)",
                     svc, len(q.get("error")))
            connect.take(svc, state, nonce)
            ok = False
        else:
            ok = await run_in_threadpool(connect.finish, svc, state, q.get("code", ""), nonce)
        if ok:
            response = RedirectResponse("/", 303, headers=no_store)
        else:
            response = PlainTextResponse(i18n.t("connect_failed"), 400, headers=no_store)
        response.delete_cookie(BIND_COOKIE, path=CALLBACKS[svc], secure=True,
                               httponly=True, samesite="lax")
        return response

    app.state.owner_connect_callback = callback  # notion_web's /notion/callback

    @app.get(CALLBACKS["home_assistant"])
    async def ha_callback(request: Request):
        return await callback("home_assistant", request)

    if getattr(app.state, "notion", None) is None:  # no family accounts: ours alone
        @app.get(CALLBACKS["notion"])
        async def notion_callback(request: Request):
            return await callback("notion", request)
