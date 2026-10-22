"""Per-turn latency: wake word -> end of speech -> transcript -> Claude's
reply -> first audio played.

Each stage is logged at INFO when it happens, and `summary()` gives one line
per turn. All times come from one monotonic clock (time.monotonic by
default; tests pass a fake one).
"""

from __future__ import annotations

import logging
import time
from typing import Callable

log = logging.getLogger(__name__)

# (key, label) in pipeline order
STAGES = (("speech_end", "end of speech"), ("transcript", "transcript"),
          ("reply", "Claude reply"), ("audio", "first audio"))
LABELS = dict(STAGES)


class TurnTimer:
    """Timestamps for one turn, measured from `start` (the wake word, or the
    moment a follow-up window opened). Only the first mark of a stage counts.
    `stages` replaces the (key, label) pipeline, e.g. for voice conversation
    mode, which streams and so has more steps."""

    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 start: str = "wake", at: float | None = None,
                 stages: tuple[tuple[str, str], ...] = STAGES):
        self.clock = clock
        self.start = start
        self.stages = stages
        self.labels = dict(stages)
        self.t0 = clock() if at is None else at
        self.marks: dict[str, float] = {}

    def mark(self, stage: str, at: float | None = None) -> None:
        if stage in self.marks:
            return
        t = self.clock() if at is None else at
        prev = max(self.marks.values(), default=self.t0)
        self.marks[stage] = t
        log.info("Timing: %s +%.2f s (%.2f s after %s)",
                 self.labels.get(stage, stage), t - prev, t - self.t0, self.start)

    def steps(self) -> list[tuple[str, float]]:
        """(label, seconds since the previous stage) for the stages reached,
        in the order they happened."""
        out, prev = [], self.t0
        reached = sorted((self.marks[k], i, label)
                         for i, (k, label) in enumerate(self.stages) if k in self.marks)
        for t, _, label in reached:
            out.append((label, t - prev))
            prev = t
        return out

    def summary(self) -> str:
        parts = [f"{label} +{seconds:.2f} s" for label, seconds in self.steps()]
        line = f"Turn timings from {self.start}: " + (", ".join(parts) or "none")
        if "audio" in self.marks:
            line += (f"; {self.start} to first audio "
                     f"{self.marks['audio'] - self.t0:.2f} s")
        if "speech_end" in self.marks and "audio" in self.marks:
            line += (f" (end of speech to first audio "
                     f"{self.marks['audio'] - self.marks['speech_end']:.2f} s)")
        return line

    def as_dict(self) -> dict[str, float]:
        """Seconds from the start to each stage reached (for the web page)."""
        return {k: round(t - self.t0, 3) for k, t in self.marks.items()}
