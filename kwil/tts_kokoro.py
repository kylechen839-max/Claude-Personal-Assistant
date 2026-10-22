"""Kokoro-82M, a small open (Apache-2.0) neural voice, via `kokoro-onnx`.

It sounds much closer to a person than Piper or the OS voice, at the cost
of more CPU: on a 4-core PC a sentence takes ~0.5 s to render (Piper ~0.1 s),
so voice mode renders sentence by sentence and the model is warmed up at
startup. On a Raspberry Pi Piper stays the default (see PROGRESS.md).

The model and voice files are fetched once, at setup time, with checksums:
    python -m kwil --download-voice kokoro
They go in models/kokoro/ (gitignored). Nothing is downloaded at request time.
"""

from __future__ import annotations

import hashlib
import logging
import queue
import re
import threading
from pathlib import Path

import numpy as np

from kwil import audio

log = logging.getLogger(__name__)

SAMPLE_RATE = 24000
DEFAULT_VOICE = "af_heart"  # warm, calm American English (see README)
DEFAULT_DIR = Path("models/kokoro")
RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
VOICES_FILE = "voices-v1.0.bin"
# Kokoro v1.0 files: name -> (sha256, size in bytes). "int8" (the default)
# is the smallest and needs the least memory (~300 MB resident with Python);
# "fp16" is a little faster on some CPUs but twice the size.
FILES = {
    "kokoro-v1.0.int8.onnx": (
        "6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb", 92361271),
    "kokoro-v1.0.fp16.onnx": (
        "c1610a859f3bdea01107e73e50100685af38fff88f5cd8e5c56df109ec880204", 177464787),
    VOICES_FILE: (
        "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d", 28214398),
}
# onnxruntime 1.23/1.24 turn the int8 model's output into noise (peaks of
# 1e29, trimmed to nothing) on some CPUs; 1.25 and later are fine.
MIN_ONNXRUNTIME = (1, 25)
VARIANTS = {"int8": "kokoro-v1.0.int8.onnx", "fp16": "kokoro-v1.0.fp16.onnx"}
# The English voices of voices-v1.0.bin (it also has other languages).
ENGLISH_VOICES = (
    "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore",
    "af_nicole", "af_nova", "af_river", "af_sarah", "af_sky", "am_adam",
    "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael", "am_onyx",
    "am_puck", "am_santa", "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
    "bm_daniel", "bm_fable", "bm_george", "bm_lewis")
# The first letter of a voice name is its language (af_ = American female).
LANGS = {"a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi",
         "i": "it", "p": "pt-br"}


CLAUSE_ENDS = ",;:-–—"


class KokoroTTS:
    """`synthesize(text)` -> (float32 samples, 24000). The model loads on
    first use (or `warm_up()`); calls are serialized by a lock. With
    `shared` (another KokoroTTS), this one speaks in its own voice with that
    one's model and lock, so a second voice doesn't load the model again
    (the phone app's voice picker)."""

    def __init__(self, model: str | Path, voices: str | Path,
                 voice: str = DEFAULT_VOICE, speed: float = 1.0,
                 shared: "KokoroTTS | None" = None):
        self.model_path = Path(model)
        self.voices_path = Path(voices)
        self.voice = voice or DEFAULT_VOICE
        self.speed = min(2.0, max(0.5, float(speed or 1.0)))
        self._kokoro = None
        self._shared = shared
        # warm-up may run in another thread
        self._lock = shared._lock if shared is not None else threading.Lock()
        self.on_audio_start = None

    @property
    def kokoro(self):
        if self._kokoro is None:
            from kokoro_onnx import Kokoro

            k = (self._shared.kokoro if self._shared is not None
                 else Kokoro(str(self.model_path), str(self.voices_path)))
            if self.voice not in k.get_voices():
                log.warning("Kokoro has no voice %r, using %s", self.voice,
                            DEFAULT_VOICE)
                self.voice = DEFAULT_VOICE
            self._kokoro = k
        return self._kokoro

    @property
    def lang(self) -> str:
        return LANGS.get(self.voice[:1], "en-us")

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        if not text.strip():
            return np.zeros(0, dtype=np.float32), SAMPLE_RATE
        with self._lock:
            samples, sr = self.kokoro.create(text, voice=self.voice,
                                             speed=self.speed, lang=self.lang)
        samples = np.asarray(samples, dtype=np.float32)
        if not len(samples) or not np.isfinite(samples).all() \
                or np.abs(samples).max() > 4:
            # never play noise; the caller reports the error instead
            raise RuntimeError("Kokoro produced no valid audio (is onnxruntime 1.25+?)")
        if text.rstrip()[-1:] in CLAUSE_ENDS:
            # Kokoro trims the pause after a clause; voice mode sends the
            # reply's first clause on its own, so put a short one back
            samples = np.concatenate([samples, np.zeros(int(sr * 0.12), np.float32)])
        return samples, sr

    def warm_up(self) -> None:
        """Load the model and run it once, so the first reply isn't slow."""
        self.synthesize("Hello.")

    def save_to_file(self, text: str, path: str | Path) -> None:
        samples, sr = self.synthesize(text)
        audio.write_wav(path, samples, sr)

    def speak(self, text: str) -> None:
        """Play sentence by sentence: the next one renders while one plays."""
        from kwil.sentences import split_sentences

        pending: queue.Queue = queue.Queue(maxsize=2)
        done = object()
        quit_render = threading.Event()  # interrupted: render no more

        def put(item) -> bool:
            while not quit_render.is_set():
                try:
                    pending.put(item, timeout=0.1)
                    return True
                except queue.Full:
                    continue
            return False

        def render():
            try:
                for sentence in split_sentences(text) or [text]:
                    if quit_render.is_set() or not put(self.synthesize(sentence)):
                        return
            except BaseException as e:  # re-raised in the playing thread
                put(e)
            put(done)

        threading.Thread(target=render, name="kokoro-render", daemon=True).start()
        started = False
        try:
            while (item := pending.get()) is not done:
                if isinstance(item, BaseException):
                    raise item
                if audio.playback_stopped():
                    return  # barge-in: don't wait for the rest to render
                samples, sr = item
                if not len(samples):
                    continue
                if not started and self.on_audio_start:
                    self.on_audio_start()
                started = True
                audio.play(samples, sr)
        finally:
            quit_render.set()


def kokoro_problem(model: str | Path | None, voices: str | Path | None) -> str | None:
    """Why Kokoro can't be used, or None if it looks usable."""
    if not model or not voices:
        return "no model set ([tts] kokoro_model / kokoro_voices)"
    for path in (Path(model), Path(voices)):
        if not path.is_file():
            return f"{path} not found"
    try:
        import kokoro_onnx  # noqa: F401
        import onnxruntime
    except ImportError:
        return ("the kokoro-onnx package is not installed (pip install -r "
                "requirements.txt; needs Python 3.11+)")
    if _version(onnxruntime.__version__) < MIN_ONNXRUNTIME:
        return (f"onnxruntime {onnxruntime.__version__} is too old for Kokoro's "
                "int8 model (needs 1.25+; older versions output noise)")
    return None


def voice_names(voices: str | Path) -> list[str]:
    """The voices in a voices-v1.0.bin (a NumPy .npz: only its file list is
    read, not the voices), or ENGLISH_VOICES if it can't be read."""
    try:
        with np.load(Path(voices), allow_pickle=False) as data:
            names = sorted(data.files)
    except Exception as e:
        log.warning("Couldn't list the Kokoro voices in %s (%s)", voices,
                    type(e).__name__)
        return list(ENGLISH_VOICES)
    return [n for n in names if re.fullmatch(r"[a-z]{2}_\w+", n)]


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", text)[:2])


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def download(dest: str | Path = DEFAULT_DIR, variant: str = "int8",
             timeout: float = 60, output_fn=print) -> tuple[Path, Path]:
    """Fetch the model and the voices into `dest`, checking each file's
    SHA-256; returns (model, voices). Verified files already there are kept.
    A file is written to .part first and only renamed once its checksum
    matches, so a bad or interrupted download never looks usable."""
    import requests

    if variant not in VARIANTS:
        raise ValueError(f"Kokoro variant should be one of {', '.join(VARIANTS)}")
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for name in (VARIANTS[variant], VOICES_FILE):
        digest, size = FILES[name]
        target = dest / name
        if target.is_file() and sha256_of(target) == digest:
            continue
        part = target.with_name(name + ".part")
        url = f"{RELEASE}/{name}"
        output_fn(f"Downloading {url} ({size / 1e6:.0f} MB)")
        log.info("Downloading %s", url)
        h = hashlib.sha256()
        try:
            with requests.get(url, stream=True, timeout=timeout) as r:
                r.raise_for_status()
                with open(part, "wb") as f:
                    for block in r.iter_content(1 << 16):
                        h.update(block)
                        f.write(block)
            if h.hexdigest() != digest:
                raise ValueError(f"checksum mismatch for {name}: got "
                                 f"{h.hexdigest()}, expected {digest}")
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        part.replace(target)
    return dest / VARIANTS[variant], dest / VOICES_FILE
