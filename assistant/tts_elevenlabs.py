"""Optional cloud voice: ElevenLabs (paid, needs ELEVENLABS_API_KEY in .env).

Only used with [tts] engine = "elevenlabs" and the key set. Each sentence is
streamed as raw 24 kHz PCM from the /stream endpoint, with a timeout; on any
error (network, quota, bad key) that sentence is rendered by the local
engine instead, and the cloud is left alone for `cooldown` seconds so a dead
connection doesn't add a timeout to every sentence. The key is sent only in
the xi-api-key header and never logged.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path

import numpy as np

from assistant import audio

log = logging.getLogger(__name__)

KEY_ENV = "ELEVENLABS_API_KEY"
API = "https://api.elevenlabs.io/v1/text-to-speech"
DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # "Rachel", a calm premade voice
DEFAULT_MODEL = "eleven_flash_v2_5"  # ElevenLabs' lowest-latency model
SAMPLE_RATE = 24000


class ElevenLabsTTS:
    def __init__(self, fallback, voice_id: str = DEFAULT_VOICE_ID,
                 model: str = DEFAULT_MODEL, speed: float = 1.0,
                 timeout: float = 8, cooldown: float = 60,
                 session=None, clock=time.monotonic):
        self.fallback = fallback
        self.voice_id = voice_id or DEFAULT_VOICE_ID
        self.model = model or DEFAULT_MODEL
        self.speed = min(1.2, max(0.7, float(speed or 1.0)))  # API range
        self.timeout = timeout
        self.cooldown = cooldown
        self.clock = clock
        self._session = session
        self._down_until = 0.0
        self.on_audio_start = None

    @property
    def session(self):
        if self._session is None:
            import requests

            self._session = requests.Session()
        return self._session

    def _cloud(self, text: str) -> np.ndarray:
        key = os.environ.get(KEY_ENV, "")
        if not key:
            raise RuntimeError(f"{KEY_ENV} is not set")
        r = self.session.post(
            f"{API}/{self.voice_id}/stream",
            params={"output_format": f"pcm_{SAMPLE_RATE}"},
            headers={"xi-api-key": key, "accept": "audio/pcm"},
            json={"text": text, "model_id": self.model,
                  "voice_settings": {"stability": 0.5, "similarity_boost": 0.75,
                                     "speed": self.speed}},
            stream=True, timeout=self.timeout)
        with r:
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            deadline = self.clock() + self.timeout * 4
            data = bytearray()
            for block in r.iter_content(1 << 14):
                data += block
                if self.clock() > deadline:
                    raise TimeoutError("ElevenLabs stream too slow")
        data = bytes(data[: len(data) - len(data) % 2])
        return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        if not text.strip():
            return np.zeros(0, dtype=np.float32), SAMPLE_RATE
        if self.clock() >= self._down_until:
            try:
                samples = self._cloud(text)
                if len(samples):
                    return samples, SAMPLE_RATE
                raise RuntimeError("no audio")
            except Exception as e:
                # type and message only: never the request, which holds the key
                log.warning("ElevenLabs failed (%s: %s); using the local voice "
                            "for %d s", type(e).__name__, e, self.cooldown)
                self._down_until = self.clock() + self.cooldown
        return local_samples(self.fallback, text)

    def warm_up(self) -> None:
        if hasattr(self.fallback, "warm_up"):
            self.fallback.warm_up()

    def save_to_file(self, text: str, path: str | Path) -> None:
        samples, sr = self.synthesize(text)
        audio.write_wav(path, samples, sr)

    def speak(self, text: str) -> None:
        samples, sr = self.synthesize(text)
        if len(samples):
            if self.on_audio_start:
                self.on_audio_start()
            audio.play(samples, sr)


def local_samples(tts, text: str) -> tuple[np.ndarray, int]:
    """Any local engine's speech as samples (via a file if it has no
    in-memory route)."""
    if hasattr(tts, "synthesize"):
        return tts.synthesize(text)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "fallback.wav"
        tts.save_to_file(text, path)
        return audio.read_wav(path)
