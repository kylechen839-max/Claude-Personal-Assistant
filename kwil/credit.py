"""The beta's starter credit: a beta tester without their own Anthropic key
talks to Claude on the owner's key until they have used `[family]
beta_credit_usd` (default $5), and all testers together at most `[family]
beta_credit_total_usd` (default $50). Then their turn stops with
userkeys.CreditSpent ("your $5 starter credit is used up; add your own
key"), before any request is made. 0 in either setting turns it off (a beta
tester without a key then gets MissingKey, as before the credit). So does a
model without a price in usage.PRICES among the ones a tester's chat can
call (`[assistant] model`, `fast_model`, `[history] title_model`): logged
at ERROR, since its calls would count as free.

The ledger is `<[family] dir>/beta_credit.json`, `{total_usd, people: {uid:
used}}`, written privately and atomically under a lock (one process: the
cloud server runs one). Removing someone drops their row but keeps
`total_usd`, so inviting people again can't reset the global cap.

How it is bounded: each call goes through family.UserApi's
CreditMessages, which, right before sending, reserves that request's own
worst case (`request_bound`: an upper bound on its prompt tokens, priced at
the model's dearest input rate, read again on every server tool use, plus
its `max_tokens` of output and its web search fees; with web search and
fetch on, about $1.80 on Sonnet even for a short chat). Checked and
reserved in one step under the lock:
  - everyone's: total used + every reservation in flight + this one must
    fit `beta_credit_total_usd`, so the global cap is never passed;
  - each person's: something must be left after their calls in flight,
    and at most MAX_IN_FLIGHT of their calls run at once. Held up only by
    calls in flight (not by money used), a call waits for them to end
    instead of being told the credit is used up. Requiring a whole worst case to fit would leave the
    last $1.80 or so of a $5 credit unusable, so a person's credit can be
    passed by at most what their last MAX_IN_FLIGHT calls cost over it
    (each at most its reservation; usually cents).
When the call ends, in a
`finally`, the reservation is swapped for what Anthropic bills: the
response's usage; for a stream cut short (Stop, barge-in, a dropped
connection) the input its snapshot reports plus the request's whole
`max_tokens` of output; with no usage at all the whole reservation. Only a
call Anthropic refused (an HTTP error status) or never received (the
connection was never made) is charged nothing. A reservation still held
after `api_timeout * (api_max_retries + 1)` seconds is charged in full; if
its call settles later the difference is put right. Reservations live in
memory; `used` persists, so a restart keeps it. Deep research, briefings
and "Sync now"'s email step never run on the credit (credit=False).

An unreadable ledger fails closed: no credit for anyone until it is fixed
(logged at ERROR, by error type).
"""

from __future__ import annotations

import base64
import itertools
import json
import logging
import threading
import time
from collections import OrderedDict
from pathlib import Path

log = logging.getLogger(__name__)

FILE = "beta_credit.json"      # in [family] dir
WEB_SEARCH_USD = 10 / 1000     # Anthropic's web search fee, $10 per 1,000 searches
HELP_PATH = "/help/anthropic-key"
# Upper bounds for what a request's content can cost in input tokens
# (request_bound). A token is at least one byte of UTF-8, so text, tool
# definitions, earlier thinking and tool results count one token a byte.
IMAGE_TOKENS = 5_000           # one image (Claude scales them down; ~1,600 at 1.15 MP)
PAGE_TOKENS = 5_000            # one PDF page: its text and its page image
MAX_PDF_PAGES = 100            # Claude's limit; a PDF whose count can't be read
SEARCH_RESULT_TOKENS = 20_000  # one web search's results (an allowance, not a bound)
KEEP_CHARGED = 1000            # expired reservations remembered for a late settle
MAX_IN_FLIGHT = 2              # a person's calls on the credit at once


def _rates(model: str, prompt_tokens: int = 0) -> tuple[float, float]:
    """($ per input token at the dearest of input / cache write / cache
    read, $ per output token) for `model`; a model without a price gets
    the dearest known rates, so it never counts as free."""
    from kwil import usage

    key = usage._price_key(model)
    if key is None:
        options = []
        for k, (p_in, p_out, p_read, p_write) in usage.PRICES.items():
            scale = usage.LONG_PROMPT.get(k, (0, 1.0))[1]
            options.append((max(p_in, p_read, p_write) * scale, p_out * scale))
        return (max(o[0] for o in options) / 1e6, max(o[1] for o in options) / 1e6)
    p_in, p_out, p_read, p_write = usage.PRICES[key]
    scale = 1.0
    if key in usage.LONG_PROMPT and prompt_tokens > usage.LONG_PROMPT[key][0]:
        scale = usage.LONG_PROMPT[key][1]
    return max(p_in, p_read, p_write) * scale / 1e6, p_out * scale / 1e6


def call_cost(model: str, counts: dict | None) -> float:
    """$ for one call's counts (usage.counts): tokens at list price plus
    the web search fee, which usage.cost leaves out. A model without a
    price is charged at the dearest known rates."""
    from kwil import usage

    if not counts:
        return 0.0
    c = {k: counts.get(k, 0) for k in ("input", "output", "cache_read", "cache_write")}
    if usage._price_key(model) is None:
        rate_in, rate_out = _rates(model)
        tokens = c["input"] + c["cache_read"] + c["cache_write"]
        tokens_usd = tokens * rate_in + c["output"] * rate_out
    else:
        tokens_usd = usage.cost(model, c)
    return tokens_usd + counts.get("web_searches", 0) * WEB_SEARCH_USD


def _bytes(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


def _pdf_pages(source: dict) -> int:
    from kwil.attachments import pdf_pages

    try:
        pages = pdf_pages(base64.b64decode(source.get("data") or ""))
    except Exception:  # noqa: BLE001 - unreadable: count the most Claude takes
        pages = None
    return pages if isinstance(pages, int) and 0 < pages <= MAX_PDF_PAGES else MAX_PDF_PAGES


def _content_tokens(content) -> int:
    """An upper bound on the input tokens of message content."""
    if isinstance(content, str):
        return len(content.encode("utf-8"))
    if isinstance(content, list):
        return sum(_content_tokens(block) for block in content)
    if not isinstance(content, dict):
        return _bytes(content)
    kind = content.get("type")
    source = content.get("source") if isinstance(content.get("source"), dict) else {}
    if kind == "image":
        return IMAGE_TOKENS
    if kind == "document":
        if source.get("type") == "base64" and source.get("media_type") == "application/pdf":
            return _pdf_pages(source) * PAGE_TOKENS
        return _bytes({k: v for k, v in content.items() if k != "source"}) \
            + _content_tokens(source.get("data") or source.get("content") or "")
    if kind == "tool_result" and isinstance(content.get("content"), list):
        rest = {k: v for k, v in content.items() if k != "content"}
        return _bytes(rest) + _content_tokens(content["content"])
    return _bytes(content)


def request_bound(kwargs: dict) -> tuple[int, int, int]:
    """(input tokens, output tokens, web searches) at most for one
    messages.create/stream call: its system, tools and messages (images
    and PDF pages at IMAGE_TOKENS / PAGE_TOKENS), plus the results its
    server tools may add, read once more on each server tool use (a
    sampling round each), and its whole max_tokens of output."""
    prompt = _bytes(kwargs.get("system") or "") + _bytes(kwargs.get("tools") or [])
    for message in kwargs.get("messages") or []:
        prompt += 8 + _content_tokens(message.get("content") if isinstance(message, dict)
                                      else message)
    searches = fetches = fetch_tokens = 0
    for tool in kwargs.get("tools") or []:
        kind = str(tool.get("type", "")) if isinstance(tool, dict) else ""
        uses = int(tool.get("max_uses") or 0) if kind else 0
        if kind.startswith("web_search"):
            searches += uses
        elif kind.startswith("web_fetch"):
            fetches += uses
            fetch_tokens += uses * int(tool.get("max_content_tokens") or 100_000)
    rounds = 1 + searches + fetches
    results = searches * SEARCH_RESULT_TOKENS + fetch_tokens
    return (prompt + results) * rounds, int(kwargs.get("max_tokens") or 0), searches


def request_reserve(kwargs: dict) -> float:
    """$ at most for one call (request_bound at its model's rates)."""
    tokens_in, tokens_out, searches = request_bound(kwargs)
    rate_in, rate_out = _rates(str(kwargs.get("model") or ""), tokens_in)
    return tokens_in * rate_in + tokens_out * rate_out + searches * WEB_SEARCH_USD


def unpriced_models(config: dict) -> list[str]:
    """The models a beta tester's conversation can call that have no price."""
    from kwil import usage

    a = config["assistant"]
    models = [a.get("model")]
    if a.get("fast_model") and str(a.get("router", "auto")).lower() != "off":
        models.append(a["fast_model"])
    models.append(config.get("history", {}).get("title_model"))
    return sorted({m for m in models if m and usage._price_key(m) is None})


class CreditLedger:
    """The starter credit's book. Thread-safe."""

    def __init__(self, config: dict, folder: str | Path | None = None,
                 monotonic=time.monotonic):
        f, a = config["family"], config["assistant"]
        self.per_person = max(0.0, float(f.get("beta_credit_usd", 0) or 0))
        self.cap = max(0.0, float(f.get("beta_credit_total_usd", 0) or 0))
        self.path = Path(folder if folder is not None else f["dir"]) / FILE
        self.ttl = float(a["api_timeout"]) * (int(a["api_max_retries"]) + 1)
        self.monotonic = monotonic
        self._lock = threading.Condition()  # settle() wakes a waiting admit()
        self._ids = itertools.count(1)
        # token -> (uid, $ held, expires at); memory only
        self._held: dict[int, tuple[str, float, float]] = {}
        # expired reservations charged in full: token -> (uid, $ charged)
        self._charged: OrderedDict[int, tuple[str, float]] = OrderedDict()
        self.total = 0.0
        self.people: dict[str, float] = {}
        self.broken = False
        self.unpriced = unpriced_models(config)
        if self.unpriced and self.per_person > 0 and self.cap > 0:
            log.error("Credit: no price for %s; the starter credit is off until "
                      "usage.PRICES has one", ", ".join(self.unpriced))
        self._load()

    @property
    def enabled(self) -> bool:
        return self.per_person > 0 and self.cap > 0 and not self.unpriced

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
        """Under the lock; an OSError is logged (memory stays right and the
        next change tries again)."""
        from kwil.tools.google_auth import write_private

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            write_private(self.path, json.dumps(
                {"total_usd": round(self.total, 6),
                 "people": {k: round(v, 6) for k, v in self.people.items()}}, indent=1))
        except OSError as e:
            log.error("Credit: couldn't save %s (%s)", self.path, type(e).__name__)

    def _charge(self, uid: str, usd: float) -> None:
        self.total += usd
        if uid in self.people:
            self.people[uid] += usd

    def _expire(self, now: float) -> None:
        """Charge reservations held past the call timeout in full: their
        call may have been billed and nobody settled it."""
        expired = [t for t, (_, _, until) in self._held.items() if until <= now]
        for token in expired:
            uid, usd, _ = self._held.pop(token)
            self._charge(uid, usd)
            self._charged[token] = (uid, usd)
            log.warning("Credit: %s's reservation of $%.4f expired unsettled; charged",
                        uid, usd)
        while len(self._charged) > KEEP_CHARGED:
            self._charged.popitem(last=False)
        if expired:
            self._save()

    def _in_flight(self, uid: str) -> tuple[float, float]:
        mine = sum(usd for who, usd, _ in self._held.values() if who == uid)
        return mine, sum(usd for _, usd, _ in self._held.values())

    def _left(self, uid: str) -> tuple[float, float]:
        """($ of `uid`'s credit, $ of everyone's) left after the calls in
        flight (under the lock)."""
        if self.broken or not self.enabled:
            return 0.0, 0.0
        mine, everyone = self._in_flight(uid)
        return (self.per_person - self.people.get(uid, 0.0) - mine,
                self.cap - self.total - everyone)

    def _spent(self, uid: str, usd: float = 0.0) -> bool:
        """Does the money already used (not what's in flight) rule out a
        call of `usd` for `uid`? Under the lock."""
        if self.broken or not self.enabled:
            return True
        return (self.people.get(uid, 0.0) >= self.per_person
                or self.total + usd > self.cap + 1e-9 or self.total >= self.cap)

    def check(self, uid: str) -> bool:
        """Is anything of `uid`'s credit (and everyone's) left, not counting
        the calls in flight (a call waits for those, admit)?"""
        with self._lock:
            self._expire(self.monotonic())
            return not self._spent(uid)

    def admit(self, uid: str, usd: float, wait: float | None = None) -> int | None:
        """Reserve `usd` (a call's worst case, request_reserve) for `uid`: a
        token for settle(), or None if it doesn't fit (their credit, or
        everyone's, is used up). When only calls in flight are in the way
        (their reservations, or MAX_IN_FLIGHT of theirs running), waits up
        to `wait` seconds (default the call timeout) for them to end."""
        usd = max(0.0, float(usd))
        deadline = time.monotonic() + (self.ttl if wait is None else wait)
        with self._lock:
            while True:
                now = self.monotonic()
                self._expire(now)
                if self._spent(uid, usd):  # the money used: CreditSpent
                    return None
                mine, everyone = self._left(uid)
                count = sum(1 for who, _, _ in self._held.values() if who == uid)
                if mine > 0 and everyone + 1e-9 >= usd and count < MAX_IN_FLIGHT:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:  # still held up by calls in flight
                    log.warning("Credit: %s's call waited for %d in flight; not sent",
                                uid, count)
                    return None
                self._lock.wait(min(remaining, 1.0))
            token = next(self._ids)
            self._held[token] = (uid, usd, now + self.ttl)
            self.people.setdefault(uid, 0.0)
        return token

    def settle(self, uid: str, usd: float, token: int | None = None) -> None:
        """Swap reservation `token` for the call's real cost (or, if it
        expired and was charged in full meanwhile, put the difference
        right). The money counts toward the global total even if `uid` was
        removed meanwhile; their own row isn't brought back."""
        usd = max(0.0, float(usd or 0.0))
        with self._lock:
            if token is not None:
                self._held.pop(token, None)
                charged = self._charged.pop(token, None)
                if charged is not None:
                    self._charge(uid, -charged[1])
            self._charge(uid, usd)
            self._save()
            self._lock.notify_all()
        log.info("Credit: %s used $%.5f (their total $%.4f, everyone $%.4f)", uid, usd,
                 self.people.get(uid, 0.0), self.total)

    def forget(self, uid: str) -> None:
        """They were removed: drop their row and reservations, keep the total."""
        with self._lock:
            had = self.people.pop(uid, None) is not None
            for token in [t for t, (who, _, _) in self._held.items() if who == uid]:
                # their call may have been billed: charged in full now, put
                # right if it settles later
                _, usd, _ = self._held.pop(token)
                self.total += usd
                self._charged[token] = (uid, usd)
            if had:
                self._save()
            self._lock.notify_all()

    def used(self, uid: str) -> float:
        with self._lock:
            return round(self.people.get(uid, 0.0), 4)

    def left(self, uid: str) -> float:
        """What their Settings shows: their credit left after their calls in
        flight, never more than what's left of everyone's."""
        with self._lock:
            return round(max(0.0, min(self._left(uid))), 4)

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
