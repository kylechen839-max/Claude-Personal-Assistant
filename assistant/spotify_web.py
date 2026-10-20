"""Connect Spotify from the phone app (the owner only).

  GET  /api/spotify/status    {"enabled", "connected"}         (owner)
  POST /api/spotify/connect   {"url": Spotify's consent page}  (owner)
  GET  /spotify/callback      ?state&code from Spotify -> token file, 303 to /

The same shape as google_web.py: the session cookie is SameSite=Strict, so
the callback is authenticated by the OAuth `state` alone, which is random,
made only for the owner's signed-in session, kept in memory (at most
MAX_PENDING, for TTL seconds) and usable once. PKCE keeps the code verifier
on this server, so a stolen code is useless. Family members get 404 from
every route here and never get the Spotify tools (their registry is their
own, family.py).

Spotify's app settings must list <public_origin>/spotify/callback as a
redirect URI (README, "Spotify").
"""

# No `from __future__ import annotations`: FastAPI must see the real Request
# class in the route signatures (it is imported inside register()).
import logging
import secrets
import threading
import time
from collections import OrderedDict

from assistant import i18n
from assistant.tools import spotify

log = logging.getLogger(__name__)

CALLBACK = "/spotify/callback"
MAX_PENDING = 4
TTL = 600  # seconds to finish signing in


class SpotifyConnect:
    """The sign-in flow, without HTTP. `tools` is the web app's shared
    ToolRegistry: once connected, the Spotify tools are added to it (every
    owner browser's SessionTools shares its dict)."""

    def __init__(self, config: dict, tools, http=None, clock=time.monotonic):
        self.config = config
        self.tools = tools
        self.http = http
        self.clock = clock
        self.pending: OrderedDict = OrderedDict()  # state -> (verifier, created)
        self.lock = threading.Lock()

    @property
    def redirect_uri(self) -> str:
        return self.config["webapp"]["public_origin"].rstrip("/") + CALLBACK

    def enabled(self) -> bool:
        return bool(self.config["spotify"]["enabled"] and spotify.client_id()
                    and self.config["webapp"]["public_origin"])

    def connected(self) -> bool:
        return spotify.load_token(self.config["spotify"]["token"]) is not None

    def start(self) -> str:
        """Spotify's consent page URL (with a PKCE challenge)."""
        state, verifier = secrets.token_urlsafe(24), spotify.new_verifier()
        with self.lock:
            self._prune()
            self.pending[state] = (verifier, self.clock())
            while len(self.pending) > MAX_PENDING:
                self.pending.popitem(last=False)
        log.info("Spotify connect started (%d pending)", len(self.pending))
        return spotify.authorize_url(spotify.client_id(), self.redirect_uri,
                                     state, verifier)

    def _prune(self) -> None:
        now = self.clock()
        for state in [s for s, (_, t) in self.pending.items() if now - t > TTL]:
            del self.pending[state]

    def drop(self, state: str) -> None:
        with self.lock:
            self.pending.pop(state, None)

    def finish(self, state: str, code: str) -> bool:
        """Exchange the code, save the token, add the tools. False (and
        nothing written) for an unknown, expired or already used state."""
        with self.lock:
            self._prune()
            entry = self.pending.pop(state, None) if state else None
        if entry is None or not code:
            log.warning("Spotify callback with an unknown or used state")
            return False
        try:
            token = spotify.exchange_code(
                code, entry[0], self.redirect_uri, spotify.client_id(),
                http=self.http, timeout=self.config["spotify"]["timeout"])
        except spotify.SpotifyError as e:
            log.warning("Spotify token exchange failed (%s)", e.reason)
            return False
        spotify.save_token(self.config["spotify"]["token"], token)
        self._register_tools()
        log.info("Spotify connected; token saved to %s", self.config["spotify"]["token"])
        return True

    def _register_tools(self) -> None:
        from assistant.cli import register_spotify
        from assistant.tools import ToolRegistry

        fresh = ToolRegistry()
        register_spotify(fresh, self.config, output_fn=lambda m: log.info("%s", m),
                         http=self.http)
        self.tools.tools.update(fresh.tools)  # one step: never half-registered
        log.info("Spotify tools added: %d", len(fresh.tools))


def register(app, connect: SpotifyConnect, owner_of, error) -> None:
    """Add the routes to a webapp.create_app() app."""
    from fastapi import Request
    from fastapi.responses import PlainTextResponse, RedirectResponse
    from starlette.concurrency import run_in_threadpool

    @app.get("/api/spotify/status")
    def spotify_status(request: Request):
        if owner_of(request) is None:
            return error("not signed in", 401)
        return {"enabled": connect.enabled(), "connected": connect.connected()}

    @app.post("/api/spotify/connect")
    async def spotify_connect(request: Request):
        if owner_of(request) is None:
            return error("not signed in", 401)
        if not connect.enabled():
            return error("Spotify connect is not set up", 503)
        return {"url": await run_in_threadpool(connect.start)}

    @app.get(CALLBACK)
    async def spotify_callback(request: Request):
        q = request.query_params
        if q.get("error"):  # e.g. access_denied; still use up the state
            log.info("Spotify sign-in not finished: %r", q.get("error")[:40])
            connect.drop(q.get("state", ""))
            ok = False
        else:
            ok = await run_in_threadpool(connect.finish, q.get("state", ""),
                                         q.get("code", ""))
        if not ok:
            return PlainTextResponse(i18n.t("spotify_failed"), 400,
                                     headers={"Cache-Control": "no-store"})
        return RedirectResponse("/", 303, headers={"Cache-Control": "no-store"})
