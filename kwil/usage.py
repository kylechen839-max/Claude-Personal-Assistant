"""API usage: the token counts of the assistant's Claude API calls (chat
turns, conversation summaries, history titles, the tutor and lecture notes),
and a rough monthly cost estimate.

Each call's numbers are logged at INFO (model and counts only, never text).
Monthly totals per model are kept in `[assistant] usage_file` (default
data/usage.json; "" = not kept), last 12 months, written privately and
atomically; processes sharing the file add up (see UsageMeter). Only the
owner sees the estimate (/api/state "month_usd").

The estimate is tokens x list prices below; web search fees are counted
(searches) but not priced, and a model not listed counts tokens at $0.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

KEEP_MONTHS = 12

# $ per million tokens: input, output, cache read, cache write (5-minute TTL).
# From the Claude API model notes (claude-api skill, shared/models.md and
# prompt-caching.md: writes 1.25x input, reads 0.1x).
PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.50),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}
# Claude Haiku 5.5 costs 5x as much when the prompt is over 100K tokens
# ($0.50 / $2.50 instead of $0.10 / $0.50).
LONG_PROMPT = {"claude-haiku-5-5": (100_000, 5.0)}

FIELDS = ("input", "output", "cache_read", "cache_write", "web_searches", "calls")


def _count(obj, name: str) -> int:
    value = obj.get(name, 0) if isinstance(obj, dict) else getattr(obj, name, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def counts(usage) -> dict[str, int]:
    """The numbers in a response's `usage` (an SDK Usage object or a dict)."""
    server = (usage.get("server_tool_use") if isinstance(usage, dict)
              else getattr(usage, "server_tool_use", None))
    return {"input": _count(usage, "input_tokens"),
            "output": _count(usage, "output_tokens"),
            "cache_read": _count(usage, "cache_read_input_tokens"),
            "cache_write": _count(usage, "cache_creation_input_tokens"),
            "web_searches": _count(server, "web_search_requests") if server else 0,
            "calls": 1}


def _price_key(model: str) -> str | None:
    model = model or ""
    return next((k for k in PRICES if model == k or model.startswith(k + "-")), None)


def cost(model: str, c: dict[str, int]) -> float:
    """Estimated $ for one call's counts."""
    key = _price_key(model)
    if key is None:
        return 0.0
    p_in, p_out, p_read, p_write = PRICES[key]
    scale = 1.0
    if key in LONG_PROMPT:
        limit, factor = LONG_PROMPT[key]
        if c["input"] + c["cache_read"] + c["cache_write"] > limit:
            scale = factor
    return scale * (c["input"] * p_in + c["output"] * p_out + c["cache_read"] * p_read
                    + c["cache_write"] * p_write) / 1_000_000


class UsageMeter:
    """Monthly totals per model; safe to call from any thread, and from
    several processes sharing one file (host wake and host web): each save
    re-reads the file under a lock file and adds only this process's calls
    since its last save."""

    def __init__(self, path: str | Path | None = None):
        self._lock = threading.Lock()
        self.path = Path(path) if path else None
        self.months: dict[str, dict[str, dict]] = {}
        self._pending: dict[str, dict[str, dict]] = {}  # not yet in the file
        if self.path is not None:
            self.months = self._read()

    def _read(self) -> dict[str, dict[str, dict]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            months = data.get("months", {}) if isinstance(data, dict) else {}
            return {m: v for m, v in months.items() if isinstance(v, dict)}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            log.warning("Usage file unreadable, starting over: %s", type(e).__name__)
            return {}

    def record(self, model: str, usage) -> dict | None:
        """Add one API call's usage; returns its counts (None without usage)."""
        if usage is None:
            return None
        c = counts(usage)
        usd = cost(model, c)
        log.info("API usage %s: input=%d output=%d cache_read=%d cache_write=%d "
                 "web_searches=%d est=$%.5f", model, c["input"], c["output"],
                 c["cache_read"], c["cache_write"], c["web_searches"], usd)
        month = datetime.now().strftime("%Y-%m")
        with self._lock:
            for months in (self.months, self._pending):
                _add(months, month, model or "unknown", c, usd)
            _cap(self.months)
            _cap(self._pending)
            if self.path is not None:
                self._save()
        return c

    def _save(self) -> None:
        """Merge this process's new calls into the file (under the lock)."""
        from kwil.tools.google_auth import write_private

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if not _acquire(self._lockfile()):
                return  # another process is saving: these calls go next time
            try:
                months = self._read()  # what the other processes added
                for month, models in self._pending.items():
                    for model, delta in models.items():
                        _add(months, month, model, delta, delta.get("usd", 0.0))
                _cap(months)
                write_private(self.path, json.dumps({"months": months}, indent=1))
                self.months, self._pending = months, {}
            finally:
                _release(self._lockfile())
        except OSError as e:  # never break a reply over the meter
            log.warning("Could not save usage (kept in memory from now on): %s",
                        type(e).__name__)
            self.path = None  # one warning, not one per API call

    def _lockfile(self) -> Path:
        return self.path.with_name(self.path.name + ".lock")

    def month_usd(self, month: str | None = None) -> float:
        """Estimated $ so far this month (or `month`, "YYYY-MM")."""
        month = month or datetime.now().strftime("%Y-%m")
        with self._lock:
            return round(sum(t.get("usd", 0.0)
                             for t in self.months.get(month, {}).values()), 4)


def _add(months: dict, month: str, model: str, c: dict, usd: float) -> None:
    totals = months.setdefault(month, {}).setdefault(
        model, dict.fromkeys(FIELDS, 0) | {"usd": 0.0})
    for k in FIELDS:
        totals[k] = totals.get(k, 0) + c.get(k, 0)
    totals["usd"] = round(totals.get("usd", 0.0) + usd, 6)


def _cap(months: dict) -> None:
    for old in sorted(months)[:-KEEP_MONTHS]:
        del months[old]


LOCK_STALE_SECONDS = 10  # a crashed process's lock file; a save takes ms
LOCK_WAIT_SECONDS = 2


def _acquire(lock: Path) -> bool:
    """An O_EXCL lock file, as updater.Updater.acquire does; one left by a
    crashed process is taken over after LOCK_STALE_SECONDS."""
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            if time.time() - lock.stat().st_mtime > LOCK_STALE_SECONDS:
                lock.unlink()
        except OSError:
            pass
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return True
        except (FileExistsError, PermissionError):
            # Windows: PermissionError while the old lock is still being
            # deleted (an antivirus or indexer handle); wait like for a lock
            if time.monotonic() > deadline:
                return False
            time.sleep(0.02)


def _release(lock: Path) -> None:
    try:
        lock.unlink()
    except OSError:
        pass


METER = UsageMeter()


def configure(path: str | Path | None) -> UsageMeter:
    """Keep monthly totals in `path` ("" or None = in memory only)."""
    global METER
    METER = UsageMeter(path)
    return METER


def record(model: str, usage) -> dict | None:
    return METER.record(model, usage)


def month_usd() -> float:
    return METER.month_usd()
