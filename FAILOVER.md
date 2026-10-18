# Failover: Pi first, then the PC, then the cloud

The phone app always tries to talk to the **Raspberry Pi**. If the Pi
can't be reached it uses the **PC**, and if neither answers it uses the
**cloud host** (Render). When a better backend comes back, the app moves
back to it, but never in the middle of a reply or a voice turn.

```
                 phone (PWA, installed from the cloud origin)
                 ├─ 1. https://<pi>.<tailnet>.ts.net     (tailscale serve)
                 ├─ 2. https://<pc>.<tailnet>.ts.net     (tailscale serve)
                 └─ 3. https://<service>.onrender.com    (same origin as the app)

   history sync (authed HTTPS, append-only event log):
        Pi ⇄ PC over the tailnet,   Pi → cloud,  PC → cloud over the public API
   shared already: Pinecone memory.   Each backend: its own copy of the keys.
```

| Piece | File |
|---|---|
| Client router (selection, failover, fail-back, override) | `assistant/web/backend-router.js` (+ `tests/js/backend-router.test.mjs`) |
| Server: `/healthz`, CORS, bearer tokens, WebSocket auth | `assistant/failover/edge.py`, `tokens.py` |
| Backend list, roles, probes, `[failover]` settings | `assistant/failover/peers.py` |
| History sync protocol (events, merge, endpoints, client) | `assistant/failover/sync.py` |
| Leader check for scheduled jobs | `assistant/failover/leader.py` |
| Tests (offline) | `tests/test_failover_*.py` |

## 1. Setting it up

On each backend, `config.toml`:

```toml
[failover]
role = "pi"                                   # "pc" on the PC, "cloud" on Render
app_origin = "https://hey-claude.onrender.com"
backends = [
  { role = "pi", url = "https://pi.tail1234.ts.net" },
  { role = "pc", url = "https://kyle-pc.tail1234.ts.net" },
  { role = "cloud", url = "https://hey-claude.onrender.com" },
]
```

`--role pi|pc|cloud` on the command line (or `ASSISTANT_ROLE` in the
environment, handy on Render) overrides `role`; `APP_ORIGIN` overrides
`app_origin`. The cloud URL defaults to `app_origin`. Leave a URL empty for
a backend you don't run. Every backend needs the **same `WEB_TOKEN`** in its
`.env` (or Render environment); that is what lets a token from one backend
work on the others.

The Pi and PC are reached through `tailscale serve` (HTTPS with a real
certificate for `*.ts.net`), so the phone must be on the tailnet (Tailscale
app on). The cloud is public.

The app's **Settings** screen has a *Backends* section: the Pi, PC and
cloud URLs (pre-filled from the server's `/api/backends` list, which the app
caches), a mode switch **Auto / Pi / PC / Cloud**, and each backend's
status (healthy, unreachable, timed out, or "incompatible API version,
update it"). The header shows a badge with the backend in use: **Pi**,
**PC**, **Cloud** or **Offline**.

## 2. Choosing a backend (client)

The app is installed from one origin, the cloud host. Its service worker
caches the app shell, so the app opens even when the cloud is down; it
then talks to whichever backend the router picks, cross-origin.

**When:** on launch, when the app comes back to the foreground
(`visibilitychange`), on `online`/network change, and every 60 s in the
background while the app is open.

**How:** `GET /healthz` on all backends **in parallel**, no credentials,
timeouts 1.5 s for the tailnet hosts and 3 s for the cloud (a sleeping
Render instance is slow). The answer must be exactly
`{"ok": true, "api": <int>, "role": "..."}` with an `api` the app supports
(`SUPPORTED_API`), otherwise that backend is skipped and Settings says why.

**Pick:** the highest-priority healthy backend. As results come in, the
router takes a backend as soon as every higher-priority one has failed; if
a lower one answers while a higher one is still pending, it waits at most
300 ms (`graceMs`) for the higher one. So a hanging Pi delays the choice by
at most 0.3 s once the PC has answered, and a fast cloud doesn't beat a Pi
that answers 100 ms later.

**Mid-request failure** (network error or HTTP 5xx; 4xx is an answer, not a
failure): the backend is marked down and the router moves to the next one
at once (a dead backend is left even mid-turn).
- Idempotent requests (GET, or explicitly marked) are retried **once** on
  the next backend. A second failure gives "no backend reachable".
- Writes (a turn, a confirmation, any tool action) are **never replayed**:
  the request might have run before the connection dropped. The router
  throws `NeedsUserRetry`; the app shows "Pi didn't answer; tap to try
  again", and the user's fresh tap goes to the new backend.

**Fail-back with hysteresis:** the background probe switches to a
higher-priority backend only after it was healthy on 2 probes in a row
(about a minute), so a flapping Pi doesn't bounce the app around.

**Never mid-turn:** the app calls `beginTurn()` when a reply or voice turn
starts and `endTurn()` when it is done. Switches decided in between (fail
back, a `select()` from a network change) wait until `endTurn()`.

**Override:** *Auto* (the default) or force *Pi*, *PC* or *Cloud*. A forced
backend is used even if it is down (requests then fail; nothing is
silently sent elsewhere), and there's no fail-back while forced.

## 3. Server side: what every backend does

`EdgeMiddleware` wraps the web app on every backend:

| Path | Auth | Answer |
|---|---|---|
| `GET /healthz` | none | `{"ok":true,"api":1,"role":"pi"}`, nothing else (no version strings, hostnames or load) |
| `POST /api/auth/token` | `{"token": WEB_TOKEN}` in the body | a derived access token (below) |
| `POST /api/auth/refresh` | app token | a new token for the same session |
| `GET /api/backends` | app token | this backend's configured list, for the app to cache |
| `/api/sync/...` | **sync** token | history sync (section 5) |
| any other `/api/...` | **app** token | the web app; a cross-origin request without a valid token → **401** |
| `/api/...` from the same origin with no `Authorization` header | the session cookie | passed on to the web app's own cookie check (the page and scripts on the cloud origin keep working as before) |
| WebSocket (any path) | token in the first message, or the cookie on the same origin | see below |
| static files (app shell) | none | the app |

**CORS:** only for the configured `app_origin`. Allowed methods `GET, POST`;
allowed request headers `Authorization, Content-Type`; no
`Access-Control-Allow-Credentials` (and any the wrapped app sets is
stripped); `Vary: Origin`; preflights cached 10 min. A request whose
`Origin` is neither the app origin nor the backend's own origin gets **403**
(also `Origin: null`, look-alikes such as
`https://hey-claude.onrender.com.evil.example`, and `http://`). Requests
with no `Origin` (other backends, curl) pass the origin check but still need
a token. The same check runs on the **WebSocket handshake**: a foreign
origin is refused before the socket opens.

**WebSocket auth:** the token is never in the URL (URLs end up in logs and
history). After the socket opens, the client's first message must be
`{"type":"auth","token":"<app token>"}`. The server answers
`{"type":"auth_ok"}` and only then hands the socket to the app. A wrong or
missing token closes it with code **4401**; nothing within 5 s closes it
with **4408**.

A same-origin WebSocket that carries the session cookie goes straight to
the web app's own `require_session` check, as without failover.

**Lockout:** the edge uses the web app's own `LoginLimiter` and client
address on each backend (per address, plus a global limit; the same one
`/api/login` uses). Only guesses at `WEB_TOKEN` count: a wrong token on
`/api/auth/token`, or a bearer that isn't a derived (`hc1.`) token.
Missing, expired or bad derived tokens don't count: they are 256-bit HMACs,
so guessing is hopeless, and an old phone or a Pi with a stale clock
retrying must not lock its owner out. Once an address is locked out, its
wrong attempts get 429; the right `WEB_TOKEN` and any valid bearer token
are always accepted.

**Clocks:** tokens are accepted up to 10 minutes past their expiry, for a
Pi that boots with a stale clock until NTP catches up. `X-Forwarded-Host`
counts as "our own host" in the origin check only from a trusted proxy
(the web app's `trusted_proxy`), like the rest of the web app.

**API version:** `assistant/failover/__init__.py` `API_VERSION` (now 1) is
bumped on any breaking change to the client API. The app accepts the range
in `SUPPORTED_API`; a backend outside it is skipped (an old Pi that hasn't
been updated yet won't be used by a newer app, and Settings says "update
Pi").

## 4. Tokens: why a derived token and not WEB_TOKEN itself

Cookies don't work here: the app's origin (Render) differs from the Pi's
and PC's, and iOS Safari blocks third-party cookies. So the app holds an
access token in **IndexedDB** and sends it as `Authorization: Bearer`.

Choice: **a derived token**, not the shared `WEB_TOKEN`.

```
hc1.<scope>.<sid>.<expires>.<signed_in>.<HMAC-SHA256(WEB_TOKEN, ...)>
```

- The user enters `WEB_TOKEN` once; the app trades it for a token
  (`POST /api/auth/token`) and **forgets WEB_TOKEN**. Every backend has the
  same `WEB_TOKEN`, so every backend can check the token offline, with no
  shared session database.
- An app token lasts 30 days and is refreshed while the app is used, but
  never past 90 days after the sign-in with `WEB_TOKEN` (`signed_in` is
  inside the signature). After that the user signs in again.
- Scopes: `app` tokens work only on the app API, `sync` tokens (which
  backends mint for themselves, valid 5 min) only on `/api/sync/`. A stolen
  phone token can't push history events or mint tokens.

Trade-offs:

| | Store WEB_TOKEN on the phone | Derived token (chosen) |
|---|---|---|
| A script injected into the app (XSS) steals... | the root secret, valid forever, on every backend | a token valid for at most 90 days |
| Revoke a phone | change WEB_TOKEN everywhere | change WEB_TOKEN everywhere (no per-device revocation: tokens are stateless) |
| Backends need | WEB_TOKEN | WEB_TOKEN (to check signatures) |
| Complexity | none | one token endpoint, one refresh |

A token in IndexedDB is readable by any script running on the app origin,
so XSS is the main risk; the page's **strict CSP** keeps scripts to the
app's own files: `default-src 'none'; script-src 'self'` (no inline script,
no `eval`), `connect-src 'self'` plus exactly the configured backend
origins (`https://...` and `wss://...`, from `edge.connect_src()`),
`frame-ancestors 'none'`, `base-uri 'none'`. Assistant replies are inserted
as text, never as HTML.

## 5. Shared state

### Memory (Pinecone)
Already shared: all backends read and write the same index.

### Conversation history
Each backend keeps its own SQLite history (the history store from
`claude/history`); they sync as an **append-only, id-addressed event log**
(`assistant/failover/sync.py`).

Every change to history is an event:

```json
{"id": "<32 hex, random>", "ts": 1792400000123, "origin": "pi",
 "kind": "message", "conv": "<conversation id>", "data": {"role": "user", "text": "..."}}
```

| kind | data | merge rule |
|---|---|---|
| `message` | `{role, text}` | ordered by (ts, id) |
| `rename` | `{title}` | last writer wins on (ts, id) |
| `pin` | `{pinned}` | last writer wins on (ts, id) |
| `delete` | `{}` | permanent tombstone for the conversation |
| `delete_message` | `{target}` | permanent tombstone for one message of the same conversation |
| `job` | `{key}` (conv `job:<key>`) | scheduled-job fires, section 5.3 |

**Deletes redact.** Besides hiding things, a tombstone removes the text
from the log itself: every event it covers (all events of the deleted
conversation, or the one deleted message) is kept with its id, ts, origin,
kind and conv but with empty data. This happens whichever arrives first:
an event that arrives after its tombstone is stored already redacted. So
once the delete has synced, the words are gone from every backend's log
and from everything it sends to peers; the ids stay, so sync still knows
those events exist and doesn't fetch them again. (Tombstones themselves
hold only ids, never text.)

The visible history is `materialize(events)`, a pure function of the
**set** of events: the same id is the same event (duplicates are ignored),
order doesn't matter, and a tombstone wins over anything, however late it
arrives. So merging is idempotent and order-independent, and any two
stores that have seen the same events show the same history. Timestamps
come from each backend's clock; ties break on the id, so every store
agrees even with skewed clocks (skew only affects which of two concurrent
renames wins).

**Transport.** A backend pushes its events to each reachable peer and pulls
the peer's, with a sync token:

```
GET  /api/sync/events?after=<seq>&limit=<n>  -> {"log", "events", "next", "more"}
POST /api/sync/events {"events": [...]}      -> {"log", "accepted"}
```

`seq` is the serving store's local insertion counter, so a pull only
returns what the puller hasn't seen, and events learned from a third
backend get a new local seq and travel on (Pi → cloud → PC). Cursors are
saved in the store. `log` is a random id per database; if a peer's changes
(Render's disk was wiped), both cursors restart from 0 and everything is
re-sent (harmless: merging is idempotent). Pi and PC sync with each other
over the tailnet and with the cloud over its public URL, every
`sync_interval` seconds (30); the cloud can't reach the tailnet, so it only
answers. Pushed events are validated (`parse_event`: id format, known
kind, size ≤ 64 KB, timestamp at most a day ahead) and a batch is accepted
all or nothing.

**Storage:** `assistant/failover/eventlog.py` `SqliteEventLog` is the
persistent log: its own `sync_events` / `sync_meta` tables, so it can share
the history database file without touching the history store's tables
(seq is AUTOINCREMENT, never reused; ids 32-hex; ts in ms; redaction on
insert). The history store records each change as an event in it and
applies the events `append` returns from peers. The same contract tests
run on it and on `MemoryEventLog`. The backends' `Syncer` and sync
endpoints use the history store's `sync_log` (an EventLog wrapper whose
`append` also projects the new events into the history tables), so
pulled events show up at once. Extra data fields the history store adds
(`kind`, `tools`, `summary`, `archived`...) travel unchanged; `materialize`
ignores them. **Storage API**: `log_id`, `append(events) -> inserted` (applying the
redactions above, see `covered_by` and `redacted`), `since(seq, limit)`,
`by_conv(conv)`, `meta_get/meta_set`. The history store owns the schema;
this module owns the protocol.

### Scheduled jobs (reminders, briefings: none exist yet)
`assistant/failover/leader.py` decides who runs a job:

1. If the shared log already has a fire for the job's dedupe key
   (e.g. `brief-2026-10-19`), skip.
2. **Leader check:** probe the `/healthz` of every higher-priority peer
   right before firing (2 tries each, so one lost probe of a flapping peer
   doesn't count as "down"). If one answers, stand down (optionally
   re-checking after a delay, in case the leader is restarting). The Pi
   never stands down. **The cloud can't reach the tailnet**, so for it a
   peer also counts as alive if it synced with the cloud in the last 2
   minutes (the sync endpoint records each caller), and it re-checks after
   more than one sync interval before firing, so a fire the Pi already
   recorded has time to arrive.
3. Record a `job` event, sync, and look again: if another backend's fire
   wins, ours is a duplicate and is **suppressed**. The winner is the
   earliest fire, except that among fires less than a minute apart (clock
   skew) the highest-priority role wins, so skewed clocks can't make two
   backends each think they were first.

Across a real partition (the PC can't reach the Pi, the Pi is up but can't
reach anyone) both may act; both fires are then in the merged log, so the
job can tell ("already reminded at 8:00 from the Pi").

## 6. Secrets on each backend

Every backend has its **own copy** of the secrets (`.env` on the Pi and PC,
environment variables and secret files on Render): `ANTHROPIC_API_KEY`,
`PINECONE_API_KEY`, `NOTION_TOKEN`, `WEB_TOKEN`, `credentials.json` and
`token.json`.

- **Anthropic:** the same key works everywhere; a separate key per backend
  (console.anthropic.com → API keys) lets you see each one's usage and
  revoke one without the others.
- **Notion:** the internal integration secret doesn't expire and works from
  any host.
- **Google Calendar:** copy the same `token.json` to each backend. Its
  **refresh token works from several hosts at once**: each host gets its
  own short-lived access tokens from it (about an hour each); refreshing
  on one host doesn't invalidate the others, and Google doesn't rotate the
  refresh token on refresh, so the copies don't drift apart (each host
  rewrites its own `token.json` with a new access token). Things that *do*
  end it, everywhere at once: revoking the app's access in your Google
  account, changing your password (for some scopes), 6 months without use,
  and, while the OAuth consent screen is in **Testing** status, a 7-day
  lifetime (publish the app, still for yourself only, to avoid weekly
  re-sign-ins). Signing in separately on each host also works, but each
  sign-in creates another refresh token (Google keeps up to 100 per
  account per client, then drops the oldest).
- **WEB_TOKEN:** must be identical on all backends. Changing it signs every
  phone out (all derived tokens stop working) and breaks sync until every
  backend has the new value.

## 7. Tests (all offline)

- `node --test tests/js/*.test.mjs`: a three-fake-backend simulation on a
  virtual clock: Pi down → PC; Pi and PC down → cloud; everything down →
  offline; parallel probes and the 1.5 s / 3 s timeouts; the grace window;
  incompatible API versions; GET retried once, writes never replayed, 5xx;
  fail-back after hysteresis and not mid-turn; a flapping Pi; override;
  the full "unplug the Pi, turn the PC off, bring both back" run.
- `tests/test_failover_edge.py`: `/healthz` contents, CORS preflight and
  rejection of foreign origins on every path, missing/wrong/expired token
  → 401 on every API path, scopes, cookie pass-through on the same origin,
  per-address lockout that never refuses the right token, token endpoint and refresh
  limit, WebSocket first-message auth (4401, 4408, token in URL ignored,
  foreign origin refused at the handshake).
- `tests/test_failover_sync.py`: merge rules, tombstones and redaction (no
  deleted text left in any store or in what it sends, in either arrival
  order, across three synced stores), validation,
  cursors, a peer's database reset, and property-style convergence: three
  stores, random events, random interleavings, duplicates and outages
  (40 seeds), always ending identical.
- `tests/test_failover_leader.py`: exactly one leader, flapping peers
  (the job runs exactly once over 80 random scenarios), partitions (double
  fires are recorded), concurrent fires suppressed, and the real topology
  (the cloud can't probe the tailnet and only hears from inbound syncs):
  the reminder goes out exactly once from the highest live backend over 40
  scenarios; without the inbound-sync signal 17 of them double-fire.
- `tests/test_failover_eventlog.py`: the SQLite log survives restarts,
  never reuses seq, shares a file with the history tables, concurrent
  appends.

## 8. Known limits

- Until the SQLite history store (claude/history) is on master and wired to
  the sync, each backend keeps its own conversation in memory: after a
  failover, Claude on the new backend doesn't see the earlier turns of the
  conversation (Pinecone memory still works).

- The phone must be on the tailnet to use the Pi or PC; on cellular without
  Tailscale it uses the cloud.
- Tokens can't be revoked one device at a time (rotate `WEB_TOKEN`).
- Deleting a conversation is permanent on all backends; a message sent to
  it concurrently from another backend is hidden too.
- History written while a backend is cut off shows up elsewhere at the next
  sync round (30 s), so right after a failover the newest turn or two from
  the old backend may be missing until it comes back.
