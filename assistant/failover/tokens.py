"""Access tokens derived from the shared WEB_TOKEN.

The phone app trades WEB_TOKEN for a derived token once (POST
/api/auth/token) and stores only the derived token. Every backend holds the
same WEB_TOKEN, so each can check a token minted by any other one without
calling it. Format (all URL-safe):

    hc1.<scope>.<sid>.<expires>.<signed_in>.<signature>

scope     "app" (the phone app) or "sync" (backend to backend)
sid       random id: the app's conversation/session id, or the peer's role
expires   unix seconds
signed_in unix seconds of the sign-in with WEB_TOKEN; refreshing keeps it,
          and a token can't be refreshed past MAX_SESSION after it
signature HMAC-SHA256(WEB_TOKEN, "hc-token:v1:<scope>:<sid>:<expires>:<signed_in>")

Revoking every token = changing WEB_TOKEN on all backends.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass

PREFIX = "hc1"
SCOPES = ("app", "sync")
APP_TTL = 30 * 24 * 3600   # phone app: 30 days unless refreshed
MAX_SESSION = 90 * 24 * 3600  # then sign in with WEB_TOKEN again
SYNC_TTL = 300             # peers mint a fresh one per sync round
LEEWAY = 600               # accept tokens this many seconds past expiry: a Pi
                           # boots with a stale clock until NTP catches up
_SID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class Claims:
    scope: str
    sid: str
    expires: int
    signed_in: int


def _sig(secret: str, scope: str, sid: str, expires: int, signed_in: int) -> str:
    msg = f"hc-token:v1:{scope}:{sid}:{expires}:{signed_in}".encode()
    mac = hmac.new(secret.encode(), msg, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode()


class TokenAuthority:
    def __init__(self, secret: str, clock=time.time, leeway: int = LEEWAY):
        if not secret or len(secret) < 32:
            raise ValueError("WEB_TOKEN must be at least 32 characters")
        self._secret = secret
        self.clock = clock
        self.leeway = leeway

    def check_root(self, given: str) -> bool:
        """Is `given` the shared WEB_TOKEN itself (constant time)?"""
        return hmac.compare_digest(given.strip().encode(), self._secret.encode())

    def mint(self, scope: str = "app", sid: str | None = None,
             ttl: int | None = None, signed_in: int | None = None) -> tuple[str, Claims]:
        if scope not in SCOPES:
            raise ValueError(f"unknown scope {scope!r}")
        sid = sid or secrets.token_urlsafe(16)
        if not _SID.match(sid):
            raise ValueError("bad sid")
        ttl = ttl if ttl is not None else (APP_TTL if scope == "app" else SYNC_TTL)
        now = int(self.clock())
        signed_in = now if signed_in is None else int(signed_in)
        expires = now + ttl
        if scope == "app":
            expires = min(expires, signed_in + MAX_SESSION)
        sig = _sig(self._secret, scope, sid, expires, signed_in)
        token = f"{PREFIX}.{scope}.{sid}.{expires}.{signed_in}.{sig}"
        return token, Claims(scope, sid, expires, signed_in)

    def verify(self, token: str | None, scope: str | None = None) -> Claims | None:
        """The token's claims if it is genuine, unexpired and (if given) of
        `scope`; else None. Never raises on garbage."""
        if not token or len(token) > 512:
            return None
        parts = token.strip().split(".")
        if len(parts) != 6 or parts[0] != PREFIX:
            return None
        _, tscope, sid, expires_s, signed_s, sig = parts
        if tscope not in SCOPES or not _SID.match(sid) or not expires_s.isdigit() \
                or not signed_s.isdigit() or len(expires_s) > 12 or len(signed_s) > 12:
            return None
        expires, signed_in = int(expires_s), int(signed_s)
        if not hmac.compare_digest(sig, _sig(self._secret, tscope, sid, expires, signed_in)):
            return None
        if expires + self.leeway <= self.clock():
            return None
        if scope is not None and tscope != scope:
            return None
        return Claims(tscope, sid, expires, signed_in)


def bearer_of(header: str | None) -> str | None:
    """The token in an `Authorization: Bearer <token>` header value."""
    if not header:
        return None
    kind, _, value = header.strip().partition(" ")
    if kind.lower() != "bearer" or not value.strip():
        return None
    return value.strip()
