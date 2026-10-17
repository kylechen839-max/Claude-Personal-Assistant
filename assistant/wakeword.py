"""Always-listening loop: wake word -> chime -> record until silence.

Audio flows as fixed-size int16 frames (512 samples at 16 kHz; openWakeWord
buffers them into its own 80 ms steps) from either the live mic
(`MicSource`) or a .wav file (`FileSource`, used by --audio-file and the
tests).
"""

from __future__ import annotations

import logging
import re
import time
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

    def drain(self) -> None:
        """Throw away anything already buffered (e.g. our own voice)."""
        if self._stream is not None:
            available = self._stream.read_available
            if available:
                self._stream.read(available)

    def frames(self) -> Iterator[np.ndarray]:
        self.start()
        while self._stream is not None:
            data, overflowed = self._stream.read(self.frame_length)
            if overflowed:
                log.debug("Mic input overflow")
            yield data[:, 0].copy()


class FileSource:
    """Plays a .wav file through the pipeline instead of the mic.

    By default the file pauses while the source is stopped. With a `clock`
    it keeps "playing" in clock time instead, and the audio that went by
    while stopped is lost, as with the real mic, which is closed while the
    assistant thinks and speaks (tests use this with a fake clock)."""

    def __init__(self, path: str | Path, frame_length: int = FRAME_LENGTH,
                 tail_silence: float = 2.0,
                 clock: Callable[[], float] | None = None):
        samples = np.concatenate([audio.load_audio(path),
                                  audio.silence(tail_silence)])
        pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
        n = len(pcm) // frame_length
        self._all = pcm[: n * frame_length].reshape(n, frame_length)
        self._frames = self._generate()
        self.frame_length = frame_length
        self.position = 0  # index of the next frame to be read
        self.clock = clock
        self._stopped_at: float | None = None

    def _generate(self) -> Iterator[np.ndarray]:
        while self.position < len(self._all):
            self.position += 1
            yield self._all[self.position - 1]

    def start(self) -> None:
        if self.clock is not None and self._stopped_at is not None:
            missed = self.clock() - self._stopped_at
            self.position += int(missed * SAMPLE_RATE / self.frame_length)
        self._stopped_at = None

    def stop(self) -> None:
        if self.clock is not None and self._stopped_at is None:
            self._stopped_at = self.clock()

    def drain(self) -> None:
        pass  # a stopped file buffers nothing

    def frames(self) -> Iterator[np.ndarray]:
        return self._frames  # shared iterator: resumes where it left off


# ---------- wake word detectors ----------
class WakeWordDetector(Protocol):
    frame_length: int

    def process(self, frame: np.ndarray) -> bool: ...


BUILTIN_KEYWORDS = ("hey_jarvis", "alexa", "hey_mycroft", "hey_rhasspy")


def phrase_from_model(path: str | Path) -> str:
    """'keywords/hey_claude_v0.1.onnx' -> 'hey claude'."""
    words = Path(path).stem.replace("-", "_").split("_")
    return " ".join(w for w in words
                    if w and not re.fullmatch(r"v\d+(\.\d+)*", w)).lower()


class OpenWakeWordDetector:
    """openWakeWord (open source, runs offline, no account or key).

    `model` is a built-in name (see BUILTIN_KEYWORDS) or a path to a custom
    .onnx model such as one trained for "Hey Claude". ONNX runs on both
    Windows and the Raspberry Pi, so one model file serves both.
    """

    def __init__(self, model: str = "hey_jarvis", sensitivity: float = 0.5):
        from openwakeword.model import Model

        custom = model.endswith(".onnx")
        if custom:
            self.phrase = phrase_from_model(model)
        elif model in BUILTIN_KEYWORDS:
            self.phrase = model.replace("_", " ")
        else:
            raise ValueError(f"Unknown wake word '{model}': use a .onnx model "
                             f"file or one of {', '.join(BUILTIN_KEYWORDS)}")
        _ensure_feature_models(None if custom else model)
        self._model = Model(wakeword_models=[model], inference_framework="onnx")
        self.threshold = 1.0 - sensitivity  # score needed to count as a wake
        self.frame_length = FRAME_LENGTH

    def process(self, frame: np.ndarray) -> bool:
        scores = self._model.predict(frame)
        if max(scores.values()) >= self.threshold:
            self._model.reset()  # don't fire again on the same utterance
            return True
        return False

    def close(self) -> None:
        pass


def _ensure_feature_models(builtin: str | None) -> None:
    """Download openWakeWord's shared models (and a built-in) on first use."""
    import openwakeword
    from openwakeword.utils import download_models

    folder = Path(openwakeword.__file__).parent / "resources" / "models"
    needed = ["melspectrogram.onnx", "embedding_model.onnx"]
    if builtin:
        needed.append(f"{builtin}_v0.1.onnx")
    if not all((folder / name).exists() for name in needed):
        log.info("Downloading openWakeWord models (one time, a few MB)")
        # names match by substring and [] means "all", so a custom model
        # passes a name that matches nothing to get only the shared models
        download_models(model_names=[builtin or "no-builtin-needed"])


def make_detector(keyword_path: str | None = None,
                  builtin_keyword: str = "hey_jarvis",
                  sensitivity: float = 0.5) -> OpenWakeWordDetector:
    if keyword_path and Path(keyword_path).exists():
        return OpenWakeWordDetector(keyword_path, sensitivity)
    if keyword_path:
        log.warning("Wake word model %s not found; using built-in '%s'",
                    keyword_path, builtin_keyword)
    return OpenWakeWordDetector(builtin_keyword, sensitivity)


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
                 no_speech_timeout: float = 5.0, ignore_after_wake_ms: int = 250,
                 clock: Callable[[], float] = time.monotonic,
                 on_follow_up: Callable[[], None] | None = None):
        self.detector = detector
        self.vad = vad or EnergyVAD()
        self.on_wake = on_wake
        self.on_follow_up = on_follow_up  # e.g. a soft chime
        self.silence_ms = silence_ms
        self.max_seconds = max_seconds
        self.no_speech_timeout = no_speech_timeout
        self.ignore_after_wake_ms = ignore_after_wake_ms
        self.clock = clock
        self.wake_time: float | None = None  # clock() when the wake word fired

    def wait_for_wake_word(self, frames: Iterator[np.ndarray]) -> bool:
        for frame in frames:
            if self.detector.process(frame):
                return True
            self.vad.is_speech(frame)  # keep the noise floor current
        return False  # source ran out

    def record_command(self, frames: Iterator[np.ndarray],
                       no_speech_timeout: float | None = None) -> np.ndarray | None:
        frame_length = self.detector.frame_length
        skip = int(self.ignore_after_wake_ms * SAMPLE_RATE / 1000 / frame_length)
        for _ in range(skip):  # let the chime echo die away
            if next(frames, None) is None:
                return None
        if no_speech_timeout is None:
            no_speech_timeout = self.no_speech_timeout
        return record_until_silence(frames, self.vad, frame_length,
                                    self.silence_ms, self.max_seconds,
                                    no_speech_timeout)

    def listen(self, source) -> tuple[bool, np.ndarray | None]:
        """Block until the wake word, then record one command.

        Returns (woke, audio). woke=False means the source is exhausted.
        """
        source.start()
        try:
            frames = iter(source.frames())
            if not self.wait_for_wake_word(frames):
                return False, None
            self.wake_time = self.clock()
            log.info("Wake word detected")
            if self.on_wake:
                self.on_wake()
            return True, self.record_command(frames)
        finally:
            source.stop()

    def record_follow_up(self, source, seconds: float) -> np.ndarray | None:
        """After a reply: record the next request without the wake word, if
        speech starts within `seconds`; None if it doesn't.

        Call it only once playback has finished. The mic is closed while the
        assistant speaks, anything still buffered is drained, and the first
        `ignore_after_wake_ms` are skipped (room echo, the chime), so our
        own voice can't come back as a follow-up."""
        if self.on_follow_up:
            self.on_follow_up()  # plays before the mic opens
        source.start()
        try:
            source.drain()
            return self.record_command(iter(source.frames()),
                                       no_speech_timeout=seconds)
        finally:
            source.stop()

    def record_reply(self, source) -> np.ndarray | None:
        """Record an answer without needing the wake word (e.g. yes/no)."""
        source.start()
        try:
            source.drain()  # not the question we just asked
            return self.record_command(iter(source.frames()))
        finally:
            source.stop()
