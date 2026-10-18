"""Backend failover: the phone app talks to the Pi first, then the PC, then
the cloud host (see FAILOVER.md).

- `tokens`: access tokens derived from WEB_TOKEN, valid on every backend.
- `edge`: ASGI middleware for /healthz, CORS (app origin only), bearer auth
  and WebSocket first-message auth.
- `peers`: roles, priority order and health probes.
- `leader`: "should this backend run a scheduled job?" check.
- `sync`: the history event log sync protocol between backends.
"""

API_VERSION = 1           # bumped on breaking changes to the client API
ROLES = ("pi", "pc", "cloud")  # priority order, highest first
