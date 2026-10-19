"""Text to speech behind a small interface so engines can be swapped.

Every engine implements `speak(text)` and `save_to_file(text, path)`.
Pick one with `make_tts(name)`: "auto" (default: the best voice whose
files are present), "kokoro" (most natural, assistant/tts_kokoro.py),
"piper" (offline neural voice, great on a Raspberry Pi), "pyttsx3" (the OS
voice), "espeak" (the espeak-ng command, for servers), "elevenlabs"
(optional paid cloud voice, assistant/tts_elevenlabs.py) or "print" (no
audio). A missing voice falls back down that list (Kokoro -> Piper ->
`fallback`, pyttsx3 by default) with one log line saying why.

Download voices: python -m assistant --download-voice kokoro
                 python -m assistant --download-voice en_US-lessac-medium
"""

from __future__ import annotations

import logging
import math
import os
import re
import sys
from pathlib import Path
from typing import Callable, Protocol

import numpy as np

from assistant import audio

log = logging.getLogger(__name__)


class TTS(Protocol):
    # Optional: called once when sound actually starts (used for timings).
    on_audio_start: Callable[[], None] | None

    def speak(self, text: str) -> None: ...

    def save_to_file(self, text: str, path: str | Path) -> None: ...


class Pyttsx3TTS:
    """Uses the OS voice: SAPI5 on Windows, eSpeak on Linux / Raspberry Pi.

    On Windows a reused pyttsx3 engine can go silent after the first
    runAndWait(), so by default a fresh engine is built for every reply
    there (`fresh_engine`); elsewhere one engine is kept. pyttsx3's
    save_to_file writes an empty .wav on Windows (its SAPI driver never
    closes the file stream properly), so files are rendered with SAPI
    directly there (`sapi_files`).
    """

    def __init__(self, rate: int = 185, voice: str | None = None,
                 fresh_engine: bool | None = None,
                 sapi_files: bool | None = None):
        self.rate = rate
        self.voice = voice
        on_windows = sys.platform == "win32"
        self.fresh_engine = on_windows if fresh_engine is None else fresh_engine
        self.sapi_files = on_windows if sapi_files is None else sapi_files
        self._engine = None
        self.on_audio_start = None

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
        hook = self.on_audio_start
        token = (engine.connect("started-utterance", lambda name=None: hook())
                 if hook else None)
        try:
            engine.say(text)
            engine.runAndWait()
        finally:
            if token is not None:
                engine.disconnect(token)

    def save_to_file(self, text: str, path: str | Path) -> None:
        if self.sapi_files:
            self._sapi_save_to_file(text, path)
            return
        engine = self.engine
        engine.save_to_file(text, str(path))
        engine.runAndWait()

    def _sapi_save_to_file(self, text: str, path: str | Path) -> None:
        import comtypes.client

        voice = comtypes.client.CreateObject("SAPI.SpVoice")
        if self.voice:
            tokens = voice.GetVoices()
            for i in range(tokens.Count):
                token = tokens.Item(i)
                if self.voice.lower() in token.GetDescription().lower():
                    voice.Voice = token
                    break
        # Same words-per-minute -> SAPI rate (-10..10) mapping as pyttsx3.
        voice.Rate = max(-10, min(10, int(math.log(self.rate / 156.63, 1.11))))
        stream = comtypes.client.CreateObject("SAPI.SpFileStream")
        stream.Open(str(Path(path).resolve()), 3)  # SSFMCreateForWrite
        try:
            voice.AudioOutputStream = stream
            voice.Speak(text, 0)  # synchronous
        finally:
            stream.Close()


class PiperTTS:
    """Offline neural voice via the `piper-tts` package (no separate install).

    `voice` is a Piper .onnx model with its .onnx.json config beside it;
    get one with `python -m assistant.tts --download-voice en_US-lessac-medium`.
    The model is loaded on first use and then kept.
    """

    def __init__(self, voice: str | Path):
        self.voice_path = Path(voice)
        self._voice = None
        self.on_audio_start = None

    def warm_up(self) -> None:
        """Load the voice now rather than on the first reply."""
        self.synthesize("Hello.")

    @property
    def voice(self):
        if self._voice is None:
            from piper import PiperVoice

            self._voice = PiperVoice.load(self.voice_path)
        return self._voice

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        """Mono float32 samples and their sample rate (22050 Hz for most voices)."""
        chunks = list(self.voice.synthesize(text))  # one chunk per sentence
        if not chunks:
            return np.zeros(0, dtype=np.float32), self.voice.config.sample_rate
        samples = np.concatenate([c.audio_float_array for c in chunks])
        return samples.astype(np.float32), chunks[0].sample_rate

    def save_to_file(self, text: str, path: str | Path) -> None:
        samples, sr = self.synthesize(text)
        audio.write_wav(path, samples, sr)

    def speak(self, text: str) -> None:
        samples, sr = self.synthesize(text)
        if len(samples):
            if self.on_audio_start:
                self.on_audio_start()
            audio.play(samples, sr)


class EspeakTTS:
    """The espeak-ng command line: robotic, but always there on a Linux
    server (the cloud image's fallback when the Piper voice is missing).
    Renders files only; there are no speakers on a server."""

    def __init__(self, rate: int = 175, voice: str | None = None,
                 command: str | None = None):
        import shutil

        self.rate = rate
        self.voice = voice or "en-us"
        self.command = command or shutil.which("espeak-ng") or shutil.which("espeak")
        if not self.command:
            raise RuntimeError("espeak-ng is not installed "
                               "(sudo apt-get install espeak-ng)")
        self.on_audio_start = None

    def save_to_file(self, text: str, path: str | Path) -> None:
        import subprocess

        # "--" so a reply starting with "-" isn't read as an option
        subprocess.run([self.command, "-v", self.voice, "-s", str(int(self.rate)),
                        "-w", str(path), "--", text], check=True, timeout=60,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def speak(self, text: str) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "say.wav"
            self.save_to_file(text, path)
            samples, sr = audio.read_wav(path)
        if self.on_audio_start:
            self.on_audio_start()
        audio.play(samples, sr)


class PrintTTS:
    """No audio: prints the text. Useful for tests and headless debugging."""

    def __init__(self, output_fn=print):
        self.output_fn = output_fn
        self.on_audio_start = None

    def speak(self, text: str) -> None:
        if self.on_audio_start:
            self.on_audio_start()
        self.output_fn(f"[speaking] {text}")

    def save_to_file(self, text: str, path: str | Path) -> None:
        audio.write_wav(path, audio.silence(0.1))


def piper_voice_problem(voice: str | Path | None) -> str | None:
    """Why a Piper voice can't be used, or None if it looks usable."""
    if not voice:
        return "no voice set ([tts] piper_voice in config.toml)"
    model = Path(voice)
    if not model.is_file():
        return f"voice file not found: {model}"
    config = model.with_name(model.name + ".json")
    if not config.is_file():
        return f"voice config not found: {config} (it goes next to the .onnx)"
    try:
        import piper  # noqa: F401
    except ImportError:
        return "the piper-tts package is not installed (pip install -r requirements.txt)"
    return None


def _is_pi_class() -> bool:
    """ARM Linux (Raspberry Pi): Kokoro is too slow there, Piper is preferred."""
    import platform

    return sys.platform.startswith("linux") and platform.machine().lower() in (
        "aarch64", "arm64", "armv7l")


def make_tts(name: str = "auto", **options) -> TTS:
    """The engine `name`, or the next one down Kokoro -> Piper -> pyttsx3 if
    its files are missing (one log line says why)."""
    from assistant import tts_kokoro

    name = (name or "auto").lower()
    piper_voice = options.get("piper_voice") or options.get("piper_model")
    kokoro_model = options.get("kokoro_model") or (
        tts_kokoro.DEFAULT_DIR / tts_kokoro.VARIANTS["int8"])
    kokoro_voices = options.get("kokoro_voices") or (
        tts_kokoro.DEFAULT_DIR / tts_kokoro.VOICES_FILE)

    def kokoro():
        return tts_kokoro.KokoroTTS(kokoro_model, kokoro_voices,
                                    voice=options.get("kokoro_voice") or "",
                                    speed=options.get("speed") or 1.0)

    def pyttsx3():
        return Pyttsx3TTS(rate=options.get("rate", 185), voice=options.get("voice"))

    # the last resort: pyttsx3, or espeak on a server (the server profile)
    fallback_name = (options.get("fallback") or "pyttsx3").lower()
    if fallback_name not in ("pyttsx3", "espeak", "print"):
        fallback_name = "pyttsx3"

    def local():
        """auto: the best local voice that is set up."""
        k = tts_kokoro.kokoro_problem(kokoro_model, kokoro_voices)
        p = piper_voice_problem(piper_voice)
        if k is None and (p is not None or not _is_pi_class()):
            log.info("TTS: Kokoro (%s)", kokoro_model)
            return kokoro()
        if p is None:
            log.info("TTS: Piper (%s)%s", piper_voice,
                     "" if k else "; Kokoro is slow on ARM, so Piper is preferred")
            return PiperTTS(piper_voice)
        log.info("TTS: %s; no Kokoro (%s) and no Piper voice (%s). For a "
                 "natural voice: python -m assistant --download-voice kokoro",
                 fallback_name, k, p)
        return make_tts(fallback_name, **options)

    if name == "elevenlabs":
        from assistant import tts_elevenlabs

        fallback = local()
        if not os.environ.get(tts_elevenlabs.KEY_ENV):
            log.warning("TTS: engine is elevenlabs but %s is not set; using "
                        "the local voice", tts_elevenlabs.KEY_ENV)
            return fallback
        return tts_elevenlabs.ElevenLabsTTS(
            fallback, voice_id=options.get("elevenlabs_voice") or "",
            model=options.get("elevenlabs_model") or "",
            speed=options.get("speed") or 1.0)
    if name == "auto":
        return local()
    if name == "kokoro":
        problem = tts_kokoro.kokoro_problem(kokoro_model, kokoro_voices)
        if problem is None:
            return kokoro()
        log.warning("Kokoro unavailable (%s), using the next voice. Download "
                    "it with: python -m assistant --download-voice kokoro", problem)
        name = "piper"
    if name == "piper":
        problem = piper_voice_problem(piper_voice)
        if problem is None:
            return PiperTTS(piper_voice)
        name = fallback_name
        log.warning("Piper voice unavailable, using %s instead: %s. "
                    "Download one with: python -m assistant.tts "
                    "--download-voice %s", name, problem, DEFAULT_PIPER_VOICE)
    if name == "espeak":
        try:
            return EspeakTTS()
        except RuntimeError as e:  # keep the server up: replies still show as text
            log.error("%s; replies will have no audio", e)
            return PrintTTS()
    if name == "pyttsx3":
        return pyttsx3()
    if name == "print":
        return PrintTTS()
    raise ValueError(f"Unknown TTS engine: {name}")


ENGINES = ("auto", "kokoro", "piper", "pyttsx3", "espeak", "elevenlabs", "print")


# Official Piper voices: https://huggingface.co/rhasspy/piper-voices
DEFAULT_PIPER_VOICE = "en_US-lessac-medium"
VOICES_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
_VOICE_NAME = re.compile(r"^(?P<lang>[a-z]{2,3})_(?P<region>[A-Z]{2})-(?P<name>[\w]+)-"
                         r"(?P<quality>x_low|low|medium|high)$")


def voice_urls(voice: str) -> dict[str, str]:
    """Download URLs for a voice like "en_US-lessac-medium", by file name."""
    m = _VOICE_NAME.match(voice.strip())
    if not m:
        raise ValueError(f"Voice {voice!r} should look like en_US-lessac-medium "
                         "(language_REGION-name-quality)")
    lang, region, name, quality = m.group("lang", "region", "name", "quality")
    base = f"{VOICES_URL}/{lang}/{lang}_{region}/{name}/{quality}/{voice.strip()}"
    return {f"{voice.strip()}{ext}": f"{base}{ext}?download=true"
            for ext in (".onnx", ".onnx.json")}


def download_voice(voice: str = DEFAULT_PIPER_VOICE, dest: str | Path = "voices",
                   timeout: float = 60) -> Path:
    """Fetch a voice's .onnx and .onnx.json into `dest`; returns the .onnx path.

    Files already there are kept. Each file is written to a .part file first,
    so an interrupted download never leaves a half-written voice behind.
    """
    import requests

    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for filename, url in voice_urls(voice).items():
        target = dest / filename
        if target.is_file() and target.stat().st_size > 0:
            continue
        part = target.with_name(target.name + ".part")
        log.info("Downloading %s", url)
        with requests.get(url, stream=True, timeout=timeout) as r:
            r.raise_for_status()
            with open(part, "wb") as f:
                for block in r.iter_content(1 << 16):
                    f.write(block)
        part.replace(target)
    return dest / f"{voice.strip()}.onnx"


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m assistant.tts",
                                     description="Piper voice helper")
    parser.add_argument("--download-voice", metavar="VOICE", nargs="?",
                        const=DEFAULT_PIPER_VOICE,
                        help=f"download a Piper voice (default {DEFAULT_PIPER_VOICE})")
    parser.add_argument("--dir", default="voices", help="where to put it (voices/)")
    parser.add_argument("--say", metavar="TEXT",
                        help="speak TEXT with the downloaded/given voice to try it")
    parser.add_argument("--voice-file", help="a .onnx voice to use with --say")
    args = parser.parse_args(argv)
    if not args.download_voice and not args.say:
        parser.print_help()
        return 1
    model = Path(args.voice_file) if args.voice_file else None
    if args.download_voice:
        model = download_voice(args.download_voice, args.dir)
        print(f"Saved {model} (and its .onnx.json). To use it, set in config.toml:\n"
              f'[tts]\nengine = "piper"\npiper_voice = "{model.as_posix()}"')
    if args.say:
        if model is None:
            parser.error("--say needs --voice-file or --download-voice")
        PiperTTS(model).speak(args.say)
    return 0


if __name__ == "__main__":
    sys.exit(main())
