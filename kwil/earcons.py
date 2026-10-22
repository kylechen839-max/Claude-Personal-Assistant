"""Soft, short interface sounds ("earcons"), generated here from sine waves.

They are original sounds made in code (no samples): mellow bell-like notes,
each a sine with a little octave overtone, a quick soft attack and an
exponential decay, so nothing clicks or beeps.

- listening: two notes rising a fifth (the mic is open)
- thinking:  one very quiet low note (your turn was heard)
- ended:     two notes falling a fifth (the conversation is over)

The phone voice view plays the WAVs in kwil/web/earcons/ (made by
`python -m kwil.earcons`; a test checks they match this code), and the
desktop wake mode plays the same listening sound as its chime.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

SAMPLE_RATE = 24000
WEB_DIR = Path(__file__).resolve().parent / "web" / "earcons"


def note(freq: float, seconds: float, sr: int = SAMPLE_RATE,
         decay: float = 9.0, attack: float = 0.012) -> np.ndarray:
    """One soft bell-like note, peak 1."""
    t = np.arange(int(sr * seconds)) / sr
    tone = np.sin(2 * np.pi * freq * t) + 0.18 * np.sin(4 * np.pi * freq * t)
    env = np.minimum(1.0, t / attack) * np.exp(-decay * t)
    out = tone * env
    fade = min(len(out), int(sr * 0.02))  # end exactly at zero
    if fade:
        out[-fade:] *= np.linspace(1.0, 0.0, fade)
    return out / np.abs(out).max()


def _sequence(notes, sr: int, gain: float) -> np.ndarray:
    """(start s, freq, length s, level) notes mixed together, scaled to `gain`."""
    length = max(start + dur for start, _, dur, _ in notes)
    out = np.zeros(int(sr * length) + 1)
    for start, freq, dur, level in notes:
        n = note(freq, dur, sr) * level
        i = int(sr * start)
        out[i:i + len(n)] += n
    return (out / np.abs(out).max() * gain).astype(np.float32)


def listening(sr: int = SAMPLE_RATE) -> np.ndarray:
    return _sequence([(0.0, 587.33, 0.22, 0.8), (0.09, 880.0, 0.26, 1.0)], sr, 0.25)


def thinking(sr: int = SAMPLE_RATE) -> np.ndarray:
    return _sequence([(0.0, 392.0, 0.16, 1.0)], sr, 0.08)


def ended(sr: int = SAMPLE_RATE) -> np.ndarray:
    return _sequence([(0.0, 880.0, 0.2, 0.9), (0.11, 587.33, 0.3, 1.0)], sr, 0.2)


EARCONS = {"listening": listening, "thinking": thinking, "ended": ended}


def write_all(dest: Path = WEB_DIR) -> list[Path]:
    from kwil import audio

    dest.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, make in EARCONS.items():
        path = dest / f"{name}.wav"
        audio.write_wav(path, make(), SAMPLE_RATE)
        paths.append(path)
    return paths


if __name__ == "__main__":
    for p in write_all(Path(sys.argv[1]) if len(sys.argv) > 1 else WEB_DIR):
        print(p)
