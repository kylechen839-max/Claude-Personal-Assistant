"""Always-listening loop: wake word -> chime -> record until silence.

Audio flows as fixed-size int16 frames (512 samples at 16 kHz; openWakeWord
buffers them into its own 80 ms steps) from either the live mic
(`MicSource`) or a .wav file (`FileSource`, used by --audio-file and the
tests).
"""

from __future__ import annotations

import itertools
import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
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

    @property
    def latency(self) -> float | None:
        """The open stream's input latency in seconds (None if unknown)."""
        try:
            return float(self._stream.latency) if self._stream is not None else None
        except (AttributeError, TypeError, ValueError):
            return None

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
    assistant thinks and speaks (tests use this with a fake clock).

    With `pace` (1.0 = real time, 4.0 = four times faster) frames come no
    faster than a mic would deliver them, so the barge-in watcher, which
    reads the file while a reply plays, hears the part that goes with it."""

    def __init__(self, path: str | Path, frame_length: int = FRAME_LENGTH,
                 tail_silence: float = 2.0,
                 clock: Callable[[], float] | None = None, pace: float = 0):
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
        self.pace = pace
        self._frame_s = frame_length / SAMPLE_RATE / pace if pace else 0.0
        self._anchor: float | None = None  # monotonic time of frame 0
        self.latency = 0.0  # like MicSource.latency (tests set a delay)

    def _generate(self) -> Iterator[np.ndarray]:
        while self.position < len(self._all):
            if self.pace:
                now = time.monotonic()
                if self._anchor is None:
                    self._anchor = now - self.position * self._frame_s
                wait = self._anchor + (self.position + 1) * self._frame_s - now
                if wait > 0:
                    time.sleep(wait)
            self.position += 1
            yield self._all[self.position - 1]

    def start(self) -> None:
        if self.clock is not None and self._stopped_at is not None:
            missed = self.clock() - self._stopped_at
            self.position += int(missed * SAMPLE_RATE / self.frame_length)
        self._stopped_at = None
        self._anchor = None  # a paused file goes on from here, in real time

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


# ---------- barge-in: talking over a reply ----------
BARGE_IN_MODES = ("wake_word", "voice", "off")
WORDS_PER_SECOND = 2.7  # spoken pace, to estimate how much of a reply was heard
CJK_CHARS_PER_SECOND = 4.0  # ... and for Chinese, in characters
DEFAULT_ECHO_DELAY = 0.35  # s, when the sound card doesn't report latency
CJK = re.compile(r"[\u4e00-\u9fff]")


class EchoGate:
    """Tells Kyle talking over a reply from the reply itself coming back
    from the speakers.

    Levels are smoothed over ~100 ms (3 frames), which bridges the short
    gaps between syllables. The echo level is the loudest the reply got
    during its first `calibrate_ms`; it then creeps up with a reply that
    gets gradually louder (never down, never jumping). The sound reaches
    the mic some time after playback starts (output + input latency: up to
    ~0.4 s with Windows MME), so audio_started() is told that delay and
    measuring starts once it has passed. It then measures for 600 ms, so an
    echo that comes later than reported (Bluetooth, some drivers: +0.25 s
    and more) is still measured rather than taken for talking. A barge-in needs
    `sustain_ms` of speech that is mostly (60% of frames) louder than
    `ratio` times the echo and than `min_level` (the room's speech
    threshold), so a cough, a door or a loud word of the reply doesn't
    count."""

    SMOOTH = 3  # frames
    MOSTLY = 0.6

    def __init__(self, min_level: float = 0.015, ratio: float = 2.5,
                 sustain_ms: int = 400, calibrate_ms: int = 600,
                 frame_length: int = FRAME_LENGTH):
        frame_ms = 1000 * frame_length / SAMPLE_RATE
        self.min_level, self.ratio = min_level, ratio
        self.sustain = max(1, round(sustain_ms / frame_ms))
        self.calibrate = max(1, round(calibrate_ms / frame_ms))
        self.frame_s = frame_ms / 1000
        self.echo = 0.0
        self.calibrating = 0  # frames of measuring left
        self.waiting = 0  # frames left until the echo reaches the mic
        self.power: deque[float] = deque(maxlen=self.SMOOTH)
        self.window: deque[bool] = deque(maxlen=self.sustain)
        self.loud = False  # the last frame counted as talking

    def audio_started(self, delay: float = 0.0) -> None:
        """The reply's sound starts now and comes back through the mic
        `delay` seconds later: measure its echo first."""
        self.calibrating = self.calibrate
        self.waiting = max(0, round(delay / self.frame_s))
        self.window.clear()

    def threshold(self) -> float:
        return max(self.min_level, self.echo * self.ratio)

    def process(self, frame: np.ndarray) -> bool:
        """True once Kyle has talked over the reply long enough."""
        self.power.append(EnergyVAD.rms(frame) ** 2)
        level = float(np.sqrt(np.mean(self.power)))
        if self.calibrating:
            self.loud = False
            if self.waiting:
                self.waiting -= 1  # the echo hasn't reached the mic yet
                return False
            self.calibrating -= 1
            self.echo = max(self.echo, level)
            self.loud = False
            return False
        self.loud = level > self.threshold()
        if self.echo < level < 1.5 * self.echo:  # the reply got a bit louder
            self.echo = 0.9 * self.echo + 0.1 * level
        self.window.append(self.loud)
        return (len(self.window) == self.sustain
                and sum(self.window) >= self.MOSTLY * self.sustain)


@dataclass
class Interruption:
    """Kyle talked over a reply (BargeInSpeaker.interruption)."""
    by: str  # "voice" or "wake word"
    recording: np.ndarray | None  # his new request; None if he said nothing
    after: float  # seconds of the reply's sound before it stopped
    spoken: str  # about how much of the reply he heard (estimated)


class BargeInSpeaker:
    """Speaks like the TTS it wraps, but listens while it speaks: when Kyle
    talks over a reply, the sound stops at once and his new request is
    recorded; `interruption` then says so (None if the reply played to
    the end).

    mode "wake_word": only the wake word stops a reply (works with speakers:
    the wake model doesn't fire on the assistant's own voice). "voice": any
    sustained speech louder than the reply's echo (EchoGate), or the wake
    word; best with headphones or an echo-cancelling speakerphone. "off":
    the mic stays closed while it speaks, as before.

    The TTS runs on the calling thread as usual; the mic is read on a
    helper thread, which stops playback with audio.stop_playback()."""

    def __init__(self, tts, listener: "WakeListener", source, mode: str = "wake_word",
                 barge_in_ms: int = 400, echo_ratio: float = 2.5,
                 calibrate_ms: int = 600, clock: Callable[[], float] = time.monotonic):
        mode = barge_in_mode(mode)
        self.tts = tts
        self.listener = listener
        self.source = source
        self.mode = mode
        self.barge_in_ms = barge_in_ms
        self.echo_ratio = echo_ratio
        self.calibrate_ms = calibrate_ms
        self.clock = clock
        self.on_audio_start = None
        self.interruption: Interruption | None = None
        if mode != "off" and hasattr(tts, "stoppable"):
            tts.stoppable = True  # pyttsx3: watch for stop_playback()

    def __getattr__(self, name):  # save_to_file, warm_up, ... of the TTS
        if name == "tts":
            raise AttributeError(name)
        return getattr(self.tts, name)

    def speak(self, text: str) -> None:
        self.interruption = None
        hook = self.on_audio_start
        if self.mode == "off":
            self._speak(text, hook)
            return
        frame_length = self.listener.detector.frame_length
        gate = EchoGate(self.listener.vad.threshold(), self.echo_ratio,
                        self.barge_in_ms, self.calibrate_ms, frame_length)
        # A reply that says the wake word itself (e.g. read from an email)
        # must not wake us up and give a command in our own voice.
        wake_ok = not says_wake_word(text, getattr(self.listener.detector, "phrase", ""))
        sound_on = threading.Event()
        done = threading.Event()
        found: dict = {}
        recent: deque = deque(maxlen=gate.sustain + 6)  # (frame, loud)

        def audio_start():
            found.setdefault("sound_at", self.clock())
            sound_on.set()
            if hook:
                hook()

        if not hasattr(self.tts, "on_audio_start"):
            audio_start()  # no report: measure the echo from the start

        audio.allow_playback()
        self.source.start()
        self.source.drain()  # nothing from before the reply
        frames = iter(self.source.frames())

        def watch():
            measuring = False
            try:
                for frame in frames:
                    if done.is_set():
                        return
                    if sound_on.is_set() and not measuring:
                        gate.audio_started(self._echo_delay())
                        measuring = True
                    by = None
                    if self.listener.detector.process(frame) and wake_ok:
                        by = "wake word"
                    elif self.mode == "voice" and gate.process(frame):
                        by = "voice"
                    recent.append((frame, gate.loud))
                    if by:
                        found["by"], found["at"] = by, self.clock()
                        audio.stop_playback()
                        return
            except Exception:  # the mic failed: the reply just plays on
                log.exception("Listening for a barge-in failed")

        watcher = threading.Thread(target=watch, name="barge-in", daemon=True)
        watcher.start()
        failed = True
        try:
            self._speak(text, audio_start)
            failed = False
        finally:
            done.set()
            watcher.join(timeout=2)  # at most a frame (it reads 32 ms at a time)
            audio.allow_playback()
            if failed:
                self.source.stop()
        if "by" not in found or watcher.is_alive():  # played to the end
            self.source.stop()
            return
        try:
            recording = self._record_rest(found["by"], recent, frames)
        finally:
            self.source.stop()
        start = found.get("sound_at")
        after = max(0.0, found["at"] - start) if start is not None else 0.0
        self.interruption = Interruption(found["by"], recording, after,
                                         heard_part(text, after))
        log.info("Barge-in (%s) after %.1f s of a %d-character reply; "
                 "%s", found["by"], after, len(text),
                 "recorded a request" if recording is not None else "no request")

    def _echo_delay(self) -> float:
        """Seconds from playback start until its sound reaches the mic: the
        output and input latencies, or a safe guess if unknown."""
        heard_in = getattr(self.source, "latency", None)
        played_out = audio.output_latency()
        if heard_in is None or played_out is None:
            return DEFAULT_ECHO_DELAY
        return float(heard_in) + float(played_out)

    def _speak(self, text: str, hook) -> None:
        if not hasattr(self.tts, "on_audio_start"):
            self.tts.speak(text)
            return
        before = self.tts.on_audio_start
        self.tts.on_audio_start = hook
        try:
            self.tts.speak(text)
        finally:
            self.tts.on_audio_start = before

    def _record_rest(self, by: str, recent: deque,
                     frames: Iterator[np.ndarray]) -> np.ndarray | None:
        """The request that interrupted the reply (playback has stopped)."""
        listener = self.listener
        if by == "wake word":  # as after any wake word: chime, then record
            listener.wake_time = listener.clock()
            if listener.on_wake:
                listener.on_wake()
            return listener.record_command(frames)
        # "voice": he is already talking; start just before he got loud, so
        # the reply's echo before that isn't transcribed with him
        held = list(recent)
        first = next((i for i, (_, loud) in enumerate(held) if loud), 0)
        start = [f for f, _ in held[max(0, first - 2):]]
        return record_until_silence(itertools.chain(start, frames), listener.vad,
                                    self.listener.detector.frame_length,
                                    listener.silence_ms, listener.max_seconds,
                                    listener.no_speech_timeout)


def says_wake_word(text: str, phrase: str | None) -> bool:
    """Does `text` contain the wake word's name ("claude" of "hey claude")?"""
    words = (phrase or "").lower().split()
    return bool(words) and words[-1] in re.findall(r"[a-z]+", text.lower())


def heard_part(text: str, seconds: float) -> str:
    """About the part of `text` said in `seconds` (spoken pace: words, or
    characters for Chinese), with "..." if it was cut off."""
    if CJK.search(text):
        n = int(seconds * CJK_CHARS_PER_SECOND)
        if n >= len(text):
            return text
        return text[:n] + "..." if n else ""
    words = text.split()
    n = min(len(words), int(seconds * WORDS_PER_SECOND))
    if n >= len(words):
        return text
    return " ".join(words[:n]) + "..." if n else ""


def barge_in_mode(value) -> str:
    """`[wake] barge_in` as one of BARGE_IN_MODES: false/"" -> "off",
    true -> "wake_word", any case; anything else is a clear error."""
    if value is None or value is False or (isinstance(value, str) and not value.strip()):
        return "off"
    if value is True:
        return "wake_word"
    mode = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if mode not in BARGE_IN_MODES:
        raise ValueError(f"[wake] barge_in must be one of "
                         f"{', '.join(repr(m) for m in BARGE_IN_MODES)}, not {value!r}")
    return mode
