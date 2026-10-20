"""Connect a Google account from the phone app ([webapp] google_connect).

For a second user (e.g. a family member's instance, config.mom.toml) who
never touches the PC: a button in the phone app's menu signs in to Google
Calendar and Gmail (read, drafts) in the phone's own browser.

  GET  /api/google/status    {"enabled", "connected"}        (signed in)
  POST /api/google/connect   {"url": Google's consent page}  (signed in)
  GET  /google/callback      ?state&code from Google -> token file, 303 to /

The session cookie is SameSite=Strict, so the browser doesn't send it on
Google's redirect back: the callback is authenticated by the OAuth `state`
alone. That state is random, made only for a signed-in session, kept in
memory (at most MAX_PENDING, for TTL seconds) and usable once; anything
else gets a 400 and writes nothing. PKCE (the code verifier stays on the
server) means a stolen code is useless without it.

Needs a "Web application" OAuth client whose authorized redirect URI is
<public_origin>/google/callback (a Desktop client only allows localhost).
"""

# No `from __future__ import annotations` here: FastAPI must see the real
# Request class in the route signatures (it is imported inside register()).
import logging
import threading
import time
from collections import OrderedDict
from pathlib import Path

from assistant import i18n
from assistant.tools.google_auth import (_relax_scope_check, client_config,
                                         load_token_info, sign_in_scopes,
                                         token_json, write_private)

log = logging.getLogger(__name__)

CALLBACK = "/google/callback"
MAX_PENDING = 4
TTL = 600  # seconds to finish signing in


class GoogleConnect:
    """The sign-in flow, without HTTP. `tools` is the web app's shared
    ToolRegistry: once signed in, Calendar and Gmail are added to it, and
    every browser's SessionTools sees them (they share its dict)."""

    def __init__(self, config: dict, tools, clock=time.monotonic):
        self.config = config
        self.tools = tools
        self.clock = clock
        self.pending: OrderedDict = OrderedDict()  # state -> (verifier, created)
        self.lock = threading.Lock()

    @property
    def _cal(self) -> dict:
        return self.config["calendar"]

    @property
    def redirect_uri(self) -> str:
        return self.config["webapp"]["public_origin"].rstrip("/") + CALLBACK

    def _client(self) -> dict | None:
        try:
            return client_config(self._cal["credentials"])
        except (OSError, ValueError):
            log.warning("Google client file %s is unreadable", self._cal["credentials"])
            return None

    def enabled(self) -> bool:
        return bool(self.config["webapp"]["public_origin"] and self._cal["enabled"]
                    and self._client())

    def connected(self) -> bool:
        try:
            return load_token_info(self._cal["token"]) is not None
        except (OSError, ValueError):
            return False

    def _flow(self, **kwargs):
        from google_auth_oauthlib.flow import Flow

        return Flow.from_client_config(
            self._client(), sign_in_scopes(self.config["gmail"]["enabled"]),
            redirect_uri=self.redirect_uri, **kwargs)

    def start(self) -> str:
        """Google's consent page URL (with a PKCE challenge)."""
        flow = self._flow()
        url, state = flow.authorization_url(access_type="offline", prompt="consent")
        with self.lock:
            self._prune()
            self.pending[state] = (flow.code_verifier, self.clock())
            while len(self.pending) > MAX_PENDING:
                self.pending.popitem(last=False)
        log.info("Google connect started (%d pending)", len(self.pending))
        return url

    def _prune(self) -> None:
        now = self.clock()
        for state in [s for s, (_, t) in self.pending.items() if now - t > TTL]:
            del self.pending[state]

    def finish(self, state: str, code: str) -> bool:
        """Exchange the code for a token, save it, add the tools. False (and
        nothing written) for an unknown, expired or already used state."""
        with self.lock:
            self._prune()
            entry = self.pending.pop(state, None) if state else None
        if entry is None or not code:
            log.warning("Google callback with an unknown or used state")
            return False
        _relax_scope_check()
        flow = self._flow(state=state, code_verifier=entry[0])
        try:
            flow.fetch_token(code=code)
        except Exception as e:  # e.g. invalid_grant; the message has no secrets
            log.warning("Google token exchange failed (%s)", type(e).__name__)
            return False
        token = Path(self._cal["token"])
        token.parent.mkdir(parents=True, exist_ok=True)
        write_private(token, token_json(flow.credentials))
        self._register_tools()
        log.info("Google account connected; token saved to %s", token)
        return True

    def _register_tools(self) -> None:
        from assistant.cli import register_google
        from assistant.tools import ToolRegistry

        fresh = ToolRegistry()
        register_google(fresh, self.config, output_fn=lambda m: log.info("%s", m))
        self.tools.tools.update(fresh.tools)  # one step: never half-registered
        log.info("Google tools added: %s", ", ".join(sorted(fresh.tools)) or "none")


def register(app, connect: GoogleConnect, session_of, error) -> None:
    """Add the routes to a webapp.create_app() app."""
    from fastapi import Request
    from fastapi.responses import PlainTextResponse, RedirectResponse
    from starlette.concurrency import run_in_threadpool

    @app.get("/api/google/status")
    def google_status(request: Request):
        if session_of(request) is None:
            return error("not signed in", 401)
        return {"enabled": connect.enabled(), "connected": connect.connected()}

    @app.post("/api/google/connect")
    async def google_connect(request: Request):
        if session_of(request) is None:
            return error("not signed in", 401)
        if not connect.enabled():
            return error("Google connect is not set up", 503)
        return {"url": await run_in_threadpool(connect.start)}

    @app.get(CALLBACK)
    async def google_callback(request: Request):
        q = request.query_params
        if q.get("error"):
            # e.g. access_denied; still use up the state
            log.info("Google sign-in not finished: %r", q.get("error")[:40])
            with connect.lock:
                connect.pending.pop(q.get("state", ""), None)
            ok = False
        else:
            ok = await run_in_threadpool(connect.finish, q.get("state", ""),
                                         q.get("code", ""))
        if not ok:
            return PlainTextResponse(i18n.t("google_failed"), 400,
                                     headers={"Cache-Control": "no-store"})
        return RedirectResponse("/", 303, headers={"Cache-Control": "no-store"})
