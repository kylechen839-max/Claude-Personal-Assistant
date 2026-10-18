"""Access tokens derived from the shared WEB_TOKEN.

The phone app trades WEB_TOKEN for a derived token once (POST
/api/auth/token) and stores only the derived token. Every backend holds the
same WEB_TOKEN, so each can check a token minted by any other one without
calling it. Format (all URL-safe):

    hc1.<scope>.<sid>.<expires>.<signature>

scope   "app" (the phone app) or "sync" (backend to backend)
sid     random id: the app's conversation/session id, or the peer's role
expires unix seconds
signature  HMAC-SHA256(WEB_TOKEN, "hc-token:v1:<scope>:<sid>:<expires>")

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
APP_TTL = 30 * 24 * 3600   # phone app: 30 days, then sign in again
SYNC_TTL = 300             # peers mint a fresh one per sync round
_SID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class Claims:
    scope: str
    sid: str
    expires: int


def _sig(secret: str, scope: str, sid: str, expires: int) -> str:
    mac = hmac.new(secret.encode(), f"hc-token:v1:{scope}:{sid}:{expires}".encode(),
                   hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode()


class TokenAuthority:
    def __init__(self, secret: str, clock=time.time):
        if not secret or len(secret) < 32:
            raise ValueError("WEB_TOKEN must be at least 32 characters")
        self._secret = secret
        self.clock = clock

    def check_root(self, given: str) -> bool:
        """Is `given` the shared WEB_TOKEN itself (constant time)?"""
        return hmac.compare_digest(given.strip().encode(), self._secret.encode())

    def mint(self, scope: str = "app", sid: str | None = None,
             ttl: int | None = None) -> tuple[str, Claims]:
        if scope not in SCOPES:
            raise ValueError(f"unknown scope {scope!r}")
        sid = sid or secrets.token_urlsafe(16)
        if not _SID.match(sid):
            raise ValueError("bad sid")
        ttl = ttl if ttl is not None else (APP_TTL if scope == "app" else SYNC_TTL)
        expires = int(self.clock()) + ttl
        token = f"{PREFIX}.{scope}.{sid}.{expires}.{_sig(self._secret, scope, sid, expires)}"
        return token, Claims(scope, sid, expires)

    def verify(self, token: str | None, scope: str | None = None) -> Claims | None:
        """The token's claims if it is genuine, unexpired and (if given) of
        `scope`; else None. Never raises on garbage."""
        if not token or len(token) > 512:
            return None
        parts = token.strip().split(".")
        if len(parts) != 5 or parts[0] != PREFIX:
            return None
        _, tscope, sid, expires_s, sig = parts
        if tscope not in SCOPES or not _SID.match(sid) or not expires_s.isdigit():
            return None
        expires = int(expires_s)
        if not hmac.compare_digest(sig, _sig(self._secret, tscope, sid, expires)):
            return None
        if expires <= self.clock():
            return None
        if scope is not None and tscope != scope:
            return None
        return Claims(tscope, sid, expires)


def bearer_of(header: str | None) -> str | None:
    """The token in an `Authorization: Bearer <token>` header value."""
    if not header:
        return None
    kind, _, value = header.strip().partition(" ")
    if kind.lower() != "bearer" or not value.strip():
        return None
    return value.strip()
