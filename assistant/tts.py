"""Text to speech behind a small interface so engines can be swapped.

Every engine implements `speak(text)` and `save_to_file(text, path)`.
Pick one with `make_tts(name)`: "pyttsx3" (offline, default), "piper"
(offline neural voice, great on a Raspberry Pi), or "print" (no audio).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Protocol

from assistant import audio

log = logging.getLogger(__name__)


class TTS(Protocol):
    def speak(self, text: str) -> None: ...

    def save_to_file(self, text: str, path: str | Path) -> None: ...


class Pyttsx3TTS:
    """Uses the OS voice: SAPI5 on Windows, eSpeak on Linux / Raspberry Pi.

    On Windows a reused pyttsx3 engine can go silent after the first
    runAndWait(), so by default a fresh engine is built for every reply
    there (`fresh_engine`); elsewhere one engine is kept.
    """

    def __init__(self, rate: int = 185, voice: str | None = None,
                 fresh_engine: bool | None = None):
        self.rate = rate
        self.voice = voice
        self.fresh_engine = (sys.platform == "win32" if fresh_engine is None
                             else fresh_engine)
        self._engine = None

    def _new_engine(self):
        from pyttsx3.engine import Engine  # pyttsx3.init() would reuse one

        engine = Engine()
        engine.setProperty("rate", self.rate)
        if self.voice:
            for v in engine.getProperty("voices"):
                if self.voice.lower() in (v.name or "").lower():
                    engine.setProperty("voice", v.id)
                    break
        return engine

    @property
    def engine(self):
        if self.fresh_engine:
            return self._new_engine()
        if self._engine is None:
            self._engine = self._new_engine()
        return self._engine

    def speak(self, text: str) -> None:
        engine = self.engine
        engine.say(text)
        engine.runAndWait()

    def save_to_file(self, text: str, path: str | Path) -> None:
        engine = self.engine
        engine.save_to_file(text, str(path))
        engine.runAndWait()


class PiperTTS:
    """Calls the `piper` command-line tool (https://github.com/rhasspy/piper)."""

    def __init__(self, model: str, executable: str = "piper"):
        self.model = model
        self.executable = executable

    def save_to_file(self, text: str, path: str | Path) -> None:
        exe = shutil.which(self.executable) or self.executable
        subprocess.run([exe, "--model", self.model, "--output_file", str(path)],
                       input=text.encode("utf-8"), check=True,
                       capture_output=True)

    def speak(self, text: str) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "reply.wav"
            self.save_to_file(text, out)
            samples, sr = audio.read_wav(out)
            audio.play(samples, sr)


class PrintTTS:
    """No audio: prints the text. Useful for tests and headless debugging."""

    def __init__(self, output_fn=print):
        self.output_fn = output_fn

    def speak(self, text: str) -> None:
        self.output_fn(f"[speaking] {text}")

    def save_to_file(self, text: str, path: str | Path) -> None:
        audio.write_wav(path, audio.silence(0.1))


def make_tts(name: str = "pyttsx3", **options) -> TTS:
    name = name.lower()
    if name == "pyttsx3":
        return Pyttsx3TTS(rate=options.get("rate", 185), voice=options.get("voice"))
    if name == "piper":
        if not options.get("piper_model"):
            raise ValueError("Piper needs tts.piper_model (path to a .onnx voice)")
        return PiperTTS(options["piper_model"],
                        options.get("piper_executable", "piper"))
    if name == "print":
        return PrintTTS()
    raise ValueError(f"Unknown TTS engine: {name}")
