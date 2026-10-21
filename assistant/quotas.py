"""Per-person limits for invited people (family.py: the "family" and "beta"
tiers), never the owner: turns per minute, turns per day and seconds of
speech transcribed per day ([family] turns_per_minute, turns_per_day,
stt_seconds_per_day; 0 = no limit).

Shaped like cloud.RateLimiter, but counted per person (their user id), not
per address, wherever they talk from (push-to-talk, typing, the voice
view; any browser or phone). Where it is checked (webapp.py, voice_ws.py):
- a typed or push-to-talk turn: before it starts (WebAssistant.start_turn);
  a recording also needs speech time left today;
- a hands-free utterance: speech time and turns are checked before Whisper
  runs, and the turn is counted just before Claude is asked (so noise
  that transcribes to nothing doesn't use up a turn);
- every recording transcribed for them (a spoken yes/no too) adds its
  length to today's speech seconds, measured from the audio before it is
  transcribed. A clip already started finishes: the day's total can go
  past the limit by at most one recording, and the next one is refused.

A refusal is a key of a sentence in i18n.py ("limit_minute", "limit_day",
"limit_speech"), said in their language; nothing is sent to Claude and
their conversation and history are not touched.

The minute is a sliding 60-second window. A day is the calendar day in the
person's own time zone (their `timezone` in users.json), or UTC if they
have none or it can't be loaded. Counts live in memory only: a restart
resets them (good enough for a beta; nothing is written to disk). Memory
stays bounded: a person with nothing counted in the current minute or day
is pruned, and at most `max_tracked` people are kept (the oldest dropped).
Logs carry ids and counts only.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

log = logging.getLogger(__name__)

WINDOW = 60.0  # seconds: "per minute"


@dataclass
class _Usage:
    minute: deque = field(default_factory=deque)  # times of turns in the window
    day: str = ""          # the person's calendar day these counts are for
    turns: int = 0         # turns that day
    speech: float = 0.0    # seconds of audio transcribed that day


class UserQuotas:
    """Thread-safe per-person counters. `clock` is wall-clock seconds
    (time.time): days are calendar days. `timezone_of(uid)` gives their
    IANA zone name, or "" / None for UTC."""

    def __init__(self, turns_per_minute: int = 6, turns_per_day: int = 200,
                 stt_seconds_per_day: float = 1800, clock=time.time,
                 timezone_of: Callable[[str], str | None] | None = None,
                 max_tracked: int = 1000):
        self.turns_per_minute = int(turns_per_minute)
        self.turns_per_day = int(turns_per_day)
        self.stt_seconds_per_day = float(stt_seconds_per_day)
        self.clock = clock
        self.timezone_of = timezone_of or (lambda uid: None)
        self.max_tracked = max(1, int(max_tracked))
        self.usage: dict[str, _Usage] = {}
        self._zones: dict[str, object] = {}
        self.lock = threading.Lock()

    @classmethod
    def from_config(cls, config: dict, **kw) -> "UserQuotas":
        f = config["family"]
        return cls(f["turns_per_minute"], f["turns_per_day"],
                   f["stt_seconds_per_day"], **kw)

    # ---------- the day ----------
    def _zone(self, name: str | None):
        if not name:
            return timezone.utc
        found = self._zones.get(name)
        if found is None:
            try:
                from zoneinfo import ZoneInfo

                found = ZoneInfo(name)
            except Exception:  # unknown or no tz database: UTC
                found = timezone.utc
            if len(self._zones) >= 64:
                self._zones.clear()
            self._zones[name] = found
        return found

    def day(self, uid: str, now: float | None = None) -> str:
        """Their calendar day (YYYY-MM-DD) at `now`, in their time zone."""
        now = self.clock() if now is None else now
        try:
            name = self.timezone_of(uid)
        except Exception:
            name = None
        return datetime.fromtimestamp(now, self._zone(name)).date().isoformat()

    # ---------- counting ----------
    def _get(self, uid: str, now: float) -> _Usage:
        """Their counters, rolled over to the current minute and day (call
        with the lock held). Makes room first: memory stays bounded."""
        u = self.usage.get(uid)
        if u is None:
            if len(self.usage) >= self.max_tracked:
                self._prune(now)
                extra = len(self.usage) - self.max_tracked + 1
                for k in list(self.usage)[:max(0, extra)]:
                    del self.usage[k]  # still full: the oldest go
            u = self.usage[uid] = _Usage()
        while u.minute and u.minute[0] <= now - WINDOW:
            u.minute.popleft()
        today = self.day(uid, now)
        if u.day != today:
            u.day, u.turns, u.speech = today, 0, 0.0
        return u

    def _prune(self, now: float) -> None:
        """Forget people with nothing counted in this minute or their day."""
        for uid, u in list(self.usage.items()):
            if (not u.minute or u.minute[-1] <= now - WINDOW) and \
                    u.day != self.day(uid, now):
                del self.usage[uid]

    def _refusal(self, u: _Usage, audio: bool) -> str | None:
        if self.turns_per_minute > 0 and len(u.minute) >= self.turns_per_minute:
            return "limit_minute"
        if self.turns_per_day > 0 and u.turns >= self.turns_per_day:
            return "limit_day"
        if audio and self.stt_seconds_per_day > 0 and \
                u.speech >= self.stt_seconds_per_day:
            return "limit_speech"
        return None

    def check(self, uid: str, audio: bool = False) -> str | None:
        """Why a turn (`audio`: one that needs Whisper) would be refused
        now, or None. Counts nothing."""
        with self.lock:
            now = self.clock()
            return self._refusal(self._get(uid, now), audio)

    def admit(self, uid: str, audio: bool = False) -> str | None:
        """check(), and when allowed count the turn. Returns the refusal
        key, or None (the turn may go ahead)."""
        with self.lock:
            now = self.clock()
            u = self._get(uid, now)
            why = self._refusal(u, audio)
            if why is None:
                u.minute.append(now)
                u.turns += 1
            return why

    def add_speech(self, uid: str, seconds: float) -> None:
        """Count `seconds` of audio transcribed for them."""
        if seconds <= 0:
            return
        with self.lock:
            u = self._get(uid, self.clock())
            u.speech += float(seconds)
            total = u.speech
        log.debug("Limits: %s has %.0f s of speech today", uid, total)

    def forget(self, uid: str) -> None:
        """Drop someone's counters (they were removed)."""
        with self.lock:
            self.usage.pop(uid, None)

    def message_values(self) -> dict:
        """The numbers the refusal sentences mention."""
        return {"per_minute": self.turns_per_minute, "per_day": self.turns_per_day,
                "minutes": max(1, round(self.stt_seconds_per_day / 60))}
