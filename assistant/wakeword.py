"""Always-listening loop: wake word -> chime -> record until silence.

Audio flows as fixed-size int16 frames (Porcupine wants 512 samples at
16 kHz) from either the live mic (`MicSource`) or a .wav file
(`FileSource`, used by --audio-file and the tests).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable, Iterator, Protocol

import numpy as np

from assistant import audio
from assistant.audio import SAMPLE_RATE

log = logging.getLogger(__name__)

FRAME_LENGTH = 512  # 32 ms at 16 kHz


# ---------- audio sources ----------
class MicSource:
    """Live microphone. Call start() before reading and stop() while busy
    (thinking/speaking) so stale audio and our own voice aren't buffered."""

    def __init__(self, frame_length: int = FRAME_LENGTH, device=None):
        self.frame_length = frame_length
        self.device = device
        self._stream = None

    def start(self) -> None:
        import sounddevice as sd

        if self._stream is None:
            self._stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                          dtype="int16", device=self.device,
                                          blocksize=self.frame_length)
            self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def frames(self) -> Iterator[np.ndarray]:
        self.start()
        while self._stream is not None:
            data, overflowed = self._stream.read(self.frame_length)
            if overflowed:
                log.debug("Mic input overflow")
            yield data[:, 0].copy()


class FileSource:
    """Plays a .wav file through the pipeline instead of the mic."""

    def __init__(self, path: str | Path, frame_length: int = FRAME_LENGTH,
                 tail_silence: float = 2.0):
        samples = np.concatenate([audio.load_audio(path),
                                  audio.silence(tail_silence)])
        pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
        n = len(pcm) // frame_length
        self._all = pcm[: n * frame_length].reshape(n, frame_length)
        self._frames = self._generate()
        self.frame_length = frame_length
        self.position = 0  # index of the next frame to be read

    def _generate(self) -> Iterator[np.ndarray]:
        while self.position < len(self._all):
            self.position += 1
            yield self._all[self.position - 1]

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def frames(self) -> Iterator[np.ndarray]:
        return self._frames  # shared iterator: resumes where it left off


# ---------- wake word detectors ----------
class WakeWordDetector(Protocol):
    frame_length: int

    def process(self, frame: np.ndarray) -> bool: ...


class PorcupineDetector:
    def __init__(self, access_key: str, keyword_path: str | None = None,
                 builtin_keyword: str = "computer", sensitivity: float = 0.6):
        import pvporcupine

        if keyword_path and Path(keyword_path).exists():
            self.phrase = Path(keyword_path).stem.split("_")[0].replace("-", " ")
            self._porcupine = pvporcupine.create(
                access_key=access_key, keyword_paths=[keyword_path],
                sensitivities=[sensitivity])
        else:
            if keyword_path:
                log.warning("Wake word file %s not found; using built-in '%s'",
                            keyword_path, builtin_keyword)
            self.phrase = builtin_keyword
            self._porcupine = pvporcupine.create(
                access_key=access_key, keywords=[builtin_keyword],
                sensitivities=[sensitivity])
        self.frame_length = self._porcupine.frame_length

    def process(self, frame: np.ndarray) -> bool:
        return self._porcupine.process(frame) >= 0

    def close(self) -> None:
        self._porcupine.delete()


def make_detector(access_key: str | None = None, keyword_path: str | None = None,
                  builtin_keyword: str = "computer",
                  sensitivity: float = 0.6) -> PorcupineDetector:
    access_key = access_key or os.environ.get("PICOVOICE_ACCESS_KEY")
    if not access_key:
        raise RuntimeError("PICOVOICE_ACCESS_KEY is not set. Get a free key at "
                           "https://console.picovoice.ai and put it in .env")
    return PorcupineDetector(access_key, keyword_path, builtin_keyword,
                             sensitivity)


# ---------- voice activity detection ----------
class EnergyVAD:
    """Simple energy-based VAD with an adaptive noise floor."""

    def __init__(self, min_threshold: float = 0.015, ratio: float = 3.0):
        self.min_threshold = min_threshold
        self.ratio = ratio
        self.noise_floor = min_threshold / ratio

    @staticmethod
    def rms(frame: np.ndarray) -> float:
        x = frame.astype(np.float32)
        if frame.dtype == np.int16:
            x /= 32768.0
        return float(np.sqrt(np.mean(x * x))) if len(x) else 0.0

    def threshold(self) -> float:
        return max(self.min_threshold, self.noise_floor * self.ratio)

    def is_speech(self, frame: np.ndarray) -> bool:
        level = self.rms(frame)
        speech = level > self.threshold()
        if not speech:  # track background noise only during non-speech
            self.noise_floor = 0.95 * self.noise_floor + 0.05 * level
        return speech


def record_until_silence(frames: Iterator[np.ndarray], vad: EnergyVAD,
                         frame_length: int = FRAME_LENGTH,
                         silence_ms: int = 800, max_seconds: float = 15.0,
                         no_speech_timeout: float = 5.0,
                         pre_roll_frames: int = 6) -> np.ndarray | None:
    """Collect frames from the first speech until `silence_ms` of quiet.

    Returns float32 16 kHz audio, or None if nobody spoke in time.
    """
    frame_ms = 1000 * frame_length / SAMPLE_RATE
    silence_needed = int(silence_ms / frame_ms)
    max_frames = int(max_seconds * 1000 / frame_ms)
    wait_frames = int(no_speech_timeout * 1000 / frame_ms)

    pre_roll: list[np.ndarray] = []
    captured: list[np.ndarray] = []
    started, quiet, waited = False, 0, 0
    for frame in frames:
        speech = vad.is_speech(frame)
        if not started:
            pre_roll = (pre_roll + [frame])[-pre_roll_frames:]
            waited += 1
            if speech:
                started, captured = True, list(pre_roll)
            elif waited >= wait_frames:
                return None
            continue
        captured.append(frame)
        quiet = 0 if speech else quiet + 1
        if quiet >= silence_needed or len(captured) >= max_frames:
            break
    if not started:
        return None
    # drop most of the trailing silence
    if quiet:
        captured = captured[: len(captured) - max(0, quiet - 3)]
    pcm = np.concatenate(captured).astype(np.float32)
    return pcm / 32768.0 if captured[0].dtype == np.int16 else pcm


# ---------- the listener ----------
class WakeListener:
    def __init__(self, detector: WakeWordDetector, vad: EnergyVAD | None = None,
                 on_wake: Callable[[], None] | None = None,
                 silence_ms: int = 800, max_seconds: float = 15.0,
                 no_speech_timeout: float = 5.0, ignore_after_wake_ms: int = 250):
        self.detector = detector
        self.vad = vad or EnergyVAD()
        self.on_wake = on_wake
        self.silence_ms = silence_ms
        self.max_seconds = max_seconds
        self.no_speech_timeout = no_speech_timeout
        self.ignore_after_wake_ms = ignore_after_wake_ms

    def wait_for_wake_word(self, frames: Iterator[np.ndarray]) -> bool:
        for frame in frames:
            if self.detector.process(frame):
                return True
            self.vad.is_speech(frame)  # keep the noise floor current
        return False  # source ran out

    def record_command(self, frames: Iterator[np.ndarray]) -> np.ndarray | None:
        frame_length = self.detector.frame_length
        skip = int(self.ignore_after_wake_ms * SAMPLE_RATE / 1000 / frame_length)
        for _ in range(skip):  # let the chime echo die away
            if next(frames, None) is None:
                return None
        return record_until_silence(frames, self.vad, frame_length,
                                    self.silence_ms, self.max_seconds,
                                    self.no_speech_timeout)

    def listen(self, source) -> tuple[bool, np.ndarray | None]:
        """Block until the wake word, then record one command.

        Returns (woke, audio). woke=False means the source is exhausted.
        """
        source.start()
        try:
            frames = iter(source.frames())
            if not self.wait_for_wake_word(frames):
                return False, None
            log.info("Wake word detected")
            if self.on_wake:
                self.on_wake()
            return True, self.record_command(frames)
        finally:
            source.stop()

    def record_reply(self, source) -> np.ndarray | None:
        """Record an answer without needing the wake word (e.g. yes/no)."""
        source.start()
        try:
            return self.record_command(iter(source.frames()))
        finally:
            source.stop()
