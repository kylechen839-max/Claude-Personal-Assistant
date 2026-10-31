"""The beta's starter credit: a beta tester without their own Anthropic key
talks to Claude on the owner's key until they have used `[family]
beta_credit_usd` (default $5), and all testers together at most `[family]
beta_credit_total_usd` (default $50). Then their turn stops with
userkeys.CreditSpent ("your $5 starter credit is used up; add your own
key"), before any request is made. 0 in either setting turns it off (a beta
tester without a key then gets MissingKey, as before the credit).

The ledger is `<[family] dir>/beta_credit.json`, `{total_usd, people: {uid:
used}}`, written privately and atomically under a lock (one process: the
cloud server runs one). Removing someone drops their row but keeps
`total_usd`, so inviting people again can't reset the global cap.

No overshoot: before each call (family.Family.api_for, via UserApi)
`admit` reserves that call's worst case, a full `[assistant]
max_history_chars` prompt (about 3 characters a token) plus `think_max_tokens`
of output and `[web] max_searches` searches, on `[assistant] model`; the
call is allowed only if the person's used + in flight + this reserve fits
their budget, and everyone's fits the global cap. `settle` (UserApi.record)
swaps the reservation for the call's real cost (usage.cost plus web search
fees). A call that fails never settles: its reservation expires after
`api_timeout * (api_max_retries + 1)` seconds. Reservations live in memory
only; `used` persists, so a restart keeps it. Deep research never runs on
the credit (research.py asks api_for(uid, credit=False)).

An unreadable ledger fails closed: no credit for anyone until it is fixed
(logged at ERROR, by error type).
"""

from __future__ import annotations

import itertools
import json
import logging
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

FILE = "beta_credit.json"      # in [family] dir
WEB_SEARCH_USD = 10 / 1000     # Anthropic's web search fee, $10 per 1,000 searches
HELP_PATH = "/help/anthropic-key"


def call_cost(model: str, counts: dict | None) -> float:
    """$ for one call's counts (usage.counts): tokens at list price plus
    the web search fee, which usage.cost leaves out."""
    from kwil import usage

    if not counts:
        return 0.0
    return usage.cost(model, counts) + counts.get("web_searches", 0) * WEB_SEARCH_USD


class CreditLedger:
    """The starter credit's book. Thread-safe."""

    def __init__(self, config: dict, folder: str | Path | None = None,
                 monotonic=time.monotonic):
        f, a = config["family"], config["assistant"]
        self.per_person = max(0.0, float(f.get("beta_credit_usd", 0) or 0))
        self.cap = max(0.0, float(f.get("beta_credit_total_usd", 0) or 0))
        self.path = Path(folder if folder is not None else f["dir"]) / FILE
        searches = int(config.get("web", {}).get("max_searches", 0) or 0)
        self.reserve = call_cost(a["model"], {
            "input": int(a["max_history_chars"]) // 3,
            "output": int(a["think_max_tokens"]),
            "cache_read": 0, "cache_write": 0, "web_searches": searches})
        self.ttl = float(a["api_timeout"]) * (int(a["api_max_retries"]) + 1)
        self.monotonic = monotonic
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        # token -> (uid, $ held, expires at); memory only
        self._held: dict[int, tuple[str, float, float]] = {}
        self.total = 0.0
        self.people: dict[str, float] = {}
        self.broken = False
        self._load()

    @property
    def enabled(self) -> bool:
        return self.per_person > 0 and self.cap > 0

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            people = data.get("people", {})
            self.people = {str(k): float(v) for k, v in people.items()
                           if isinstance(v, (int, float)) and not isinstance(v, bool)}
            total = data.get("total_usd", 0)
            if isinstance(total, bool) or not isinstance(total, (int, float)):
                raise ValueError("total_usd")
            self.total = max(float(total), sum(self.people.values()))
        except FileNotFoundError:
            return
        except (OSError, ValueError, AttributeError, TypeError) as e:
            log.error("Credit: %s is unreadable (%s); no starter credit until it is "
                      "fixed", self.path, type(e).__name__)
            self.broken = True

    def _save(self) -> None:
        from kwil.tools.google_auth import write_private

        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_private(self.path, json.dumps(
            {"total_usd": round(self.total, 6),
             "people": {k: round(v, 6) for k, v in self.people.items()}}, indent=1))

    def _expire(self, now: float) -> None:
        for token in [t for t, (_, _, until) in self._held.items() if until <= now]:
            del self._held[token]

    def _fits(self, uid: str) -> bool:
        """Under the lock, expired reservations dropped."""
        if self.broken or not self.enabled:
            return False
        mine = sum(usd for who, usd, _ in self._held.values() if who == uid)
        everyone = sum(usd for _, usd, _ in self._held.values())
        return (self.people.get(uid, 0.0) + mine + self.reserve <= self.per_person + 1e-9
                and self.total + everyone + self.reserve <= self.cap + 1e-9)

    def check(self, uid: str) -> bool:
        """Would a call for `uid` be admitted now? Reserves nothing."""
        with self._lock:
            self._expire(self.monotonic())
            return self._fits(uid)

    def admit(self, uid: str) -> int | None:
        """Reserve one call's worst case for `uid`: a token for settle(), or
        None if it doesn't fit (their credit, or everyone's, is used up)."""
        with self._lock:
            now = self.monotonic()
            self._expire(now)
            if not self._fits(uid):
                return None
            token = next(self._ids)
            self._held[token] = (uid, self.reserve, now + self.ttl)
            self.people.setdefault(uid, 0.0)
        return token

    def settle(self, uid: str, usd: float, token: int | None = None) -> None:
        """Swap reservation `token` (if still held) for the call's real cost.
        The money counts toward the global total even if `uid` was removed
        meanwhile; their own row isn't brought back."""
        usd = max(0.0, float(usd or 0.0))
        with self._lock:
            if token is not None:
                self._held.pop(token, None)
            self.total += usd
            if uid in self.people:
                self.people[uid] += usd
            try:
                self._save()
            except OSError as e:  # the next settle tries again; memory is right
                log.error("Credit: couldn't save %s (%s)", self.path, type(e).__name__)
        log.info("Credit: %s used $%.5f (their total $%.4f, everyone $%.4f)", uid, usd,
                 self.people.get(uid, 0.0), self.total)

    def forget(self, uid: str) -> None:
        """They were removed: drop their row and reservations, keep the total."""
        with self._lock:
            had = self.people.pop(uid, None) is not None
            for token in [t for t, (who, _, _) in self._held.items() if who == uid]:
                del self._held[token]
            if had:
                try:
                    self._save()
                except OSError as e:
                    log.error("Credit: couldn't save %s (%s)", self.path, type(e).__name__)

    def used(self, uid: str) -> float:
        with self._lock:
            return round(self.people.get(uid, 0.0), 4)

    def left(self, uid: str) -> float:
        """What their Settings shows: their credit left, never more than
        what's left of everyone's."""
        if not self.enabled or self.broken:
            return 0.0
        with self._lock:
            mine = self.per_person - self.people.get(uid, 0.0)
            return round(max(0.0, min(mine, self.cap - self.total)), 4)

    def totals(self) -> dict:
        """For the owner's list of people."""
        with self._lock:
            return {"credit_total_used": round(self.total, 4),
                    "credit_total_cap": self.cap if self.enabled else 0.0}

    def help_url(self, config: dict) -> str:
        """The key guide's address for the "credit used up" sentence, or ""
        (no public site here, or this server's address unknown)."""
        if not config.get("webapp", {}).get("public_site"):
            return ""
        from kwil.family import own_origin

        origin = own_origin(config)
        return f"{origin.rstrip('/')}{HELP_PATH}" if origin else ""
