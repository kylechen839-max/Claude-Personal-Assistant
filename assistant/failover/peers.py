"""Roles, the backend list and /healthz probes (server side)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from urllib.parse import urlsplit

from assistant.failover import API_VERSION, ROLES

log = logging.getLogger(__name__)

TAILNET_TIMEOUT = 1.5   # seconds; the Pi and PC answer fast or not at all
CLOUD_TIMEOUT = 3.0     # a sleeping Render instance is slower


@dataclass(frozen=True)
class Backend:
    role: str
    url: str  # https://host[:port], no trailing slash

    @property
    def priority(self) -> int:
        return ROLES.index(self.role)

    @property
    def timeout(self) -> float:
        return CLOUD_TIMEOUT if self.role == "cloud" else TAILNET_TIMEOUT


@dataclass(frozen=True)
class Health:
    ok: bool
    api: int | None = None
    role: str | None = None
    reason: str = ""

    @property
    def usable(self) -> bool:
        return self.ok and api_compatible(self.api)


def api_compatible(api) -> bool:
    return isinstance(api, int) and not isinstance(api, bool) and api == API_VERSION


def normalize_url(url: str) -> str:
    """'https://host[:port]' or ValueError. Plain http is allowed only for
    localhost (tests, a Pi used on the same machine)."""
    parts = urlsplit(str(url).strip())
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("https", "http") or not host:
        raise ValueError(f"not an http(s) URL: {url!r}")
    if parts.scheme == "http" and host not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError(f"backends must use https: {url!r}")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError(f"unexpected parts in backend URL: {url!r}")
    if parts.path not in ("", "/"):
        raise ValueError(f"backend URL must not have a path: {url!r}")
    netloc = host if parts.port is None else f"{host}:{parts.port}"
    if ":" in host:  # IPv6
        netloc = f"[{host}]" if parts.port is None else f"[{host}]:{parts.port}"
    return f"{parts.scheme}://{netloc}"


def parse_backends(entries) -> list[Backend]:
    """[{role, url}, ...] from config -> Backends in priority order; empty
    URLs are skipped (that backend isn't set up)."""
    out: dict[str, Backend] = {}
    for entry in entries or []:
        role = str(entry.get("role", "")).strip().lower()
        url = str(entry.get("url", "")).strip()
        if role not in ROLES:
            raise ValueError(f"unknown backend role {role!r} (use pi, pc or cloud)")
        if url:
            out[role] = Backend(role, normalize_url(url))
    return sorted(out.values(), key=lambda b: b.priority)


def parse_health(status: int, body) -> Health:
    if status != 200 or not isinstance(body, dict):
        return Health(False, reason=f"HTTP {status}")
    if body.get("ok") is not True:
        return Health(False, reason="not ok")
    api, role = body.get("api"), body.get("role")
    if not api_compatible(api):
        return Health(True, api if isinstance(api, int) else None, role,
                      reason=f"incompatible API version {api!r} (need {API_VERSION})")
    return Health(True, api, role if role in ROLES else None)


def probe(backend: Backend, session=None, timeout: float | None = None) -> Health:
    """GET <backend>/healthz with a short timeout. Never raises."""
    import requests

    getter = session.get if session is not None else requests.get
    try:
        r = getter(backend.url + "/healthz", timeout=timeout or backend.timeout,
                   allow_redirects=False)
        try:
            body = r.json()
        except ValueError:
            body = None
        return parse_health(r.status_code, body)
    except Exception as e:  # connection refused, DNS, timeout, TLS...
        log.debug("Probe of %s failed: %s", backend.role, type(e).__name__)
        return Health(False, reason=type(e).__name__)


@dataclass(frozen=True)
class FailoverSettings:
    role: str                 # "" when this install isn't part of a failover setup
    app_origin: str | None
    backends: tuple[Backend, ...]

    @property
    def peers(self) -> list[Backend]:
        return [b for b in self.backends if b.role != self.role]


def settings_from_config(config: dict) -> FailoverSettings:
    """[failover] from config.toml, validated. The cloud backend's URL
    defaults to app_origin (the app is installed from the cloud host)."""
    f = config.get("failover", {})
    role = str(f.get("role", "") or "").strip().lower()
    if role and role not in ROLES:
        raise ValueError(f"[failover] role must be pi, pc or cloud, not {role!r}")
    origin = normalize_url(f["app_origin"]) if f.get("app_origin") else None
    entries = list(f.get("backends") or [])
    env = os.environ.get("FAILOVER_BACKENDS", "").strip()
    if env:  # e.g. on Render: "pi=https://pi.x.ts.net,pc=https://pc.x.ts.net"
        entries = []
        for item in env.split(","):
            role, _, url = item.strip().partition("=")
            if role:
                entries.append({"role": role.strip(), "url": url.strip()})
    if origin and not any(e.get("role") == "cloud" and e.get("url") for e in entries):
        entries = [e for e in entries if e.get("role") != "cloud"]
        entries.append({"role": "cloud", "url": origin})
    return FailoverSettings(role, origin, tuple(parse_backends(entries)))
