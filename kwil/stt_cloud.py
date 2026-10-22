"""Optional cloud speech-to-text: OpenAI (gpt-4o-transcribe) or Deepgram
(nova-3). Paid; needs OPENAI_API_KEY or DEEPGRAM_API_KEY in .env.

Only used with [stt] engine = "openai" or "deepgram" and that key set.
Each recording is sent once as a 16 kHz WAV with a timeout, together with
the app's vocabulary (OpenAI: the prompt; Deepgram: keyterms). On any error
(network, quota, bad key, timeout, an odd reply) that recording is
transcribed by local Whisper instead, and the cloud is left alone for
`cooldown` seconds so a dead connection doesn't add a timeout to every
turn. Mirrors tts_elevenlabs.py. The key goes only in the Authorization
header; it, the audio, the transcript and response bodies are never
logged (the transcript at DEBUG only, as local Whisper does).

Invited people's speech counts against their [family] stt_seconds_per_day
before it gets here (quotas.py, measured from the audio), whichever engine
transcribes it.
"""

from __future__ import annotations

import io
import logging
import os
import re
import time

import numpy as np

from kwil import audio

log = logging.getLogger(__name__)

KEY_ENV = {"openai": "OPENAI_API_KEY", "deepgram": "DEEPGRAM_API_KEY"}
DEFAULT_MODELS = {"openai": "gpt-4o-transcribe", "deepgram": "nova-3"}
OPENAI_URL = "https://api.openai.com/v1/audio/transcriptions"
DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"
DEFAULT_TIMEOUT = 10.0
MAX_TIMEOUT = 60.0
MAX_SECONDS = 300  # longer recordings stay local (cost; OpenAI's 25 MB cap)
MAX_KEYTERMS = 50


class CloudSTTError(RuntimeError):
    """A failure whose message is safe to log (fixed words and a status)."""


class CloudTranscriber:
    """A Transcriber that asks the cloud first and falls back to `local`
    (an stt.Transcriber, whose language, prompt and hotwords it shares)."""

    def __init__(self, local, engine: str, model: str = "",
                 timeout: float | None = DEFAULT_TIMEOUT, cooldown: float = 60,
                 session=None, clock=time.monotonic, environ=os.environ):
        self.local = local
        self.engine = engine if engine in KEY_ENV else None
        if self.engine is None:
            log.warning("Unknown [stt] engine %r; using local Whisper", engine)
        self.cloud_model = model or DEFAULT_MODELS.get(self.engine or "", "")
        timeout = float(timeout) if timeout else DEFAULT_TIMEOUT  # 0 = default
        self.timeout = min(MAX_TIMEOUT, max(1.0, timeout))
        self.cooldown = cooldown
        self.clock = clock
        self.environ = environ
        self._session = session
        self._down_until = 0.0
        self._warned_key = False

    # what callers read off a Transcriber (warm-up, the lecture job, sessions)
    @property
    def model(self):
        return self.local.model

    @property
    def model_name(self) -> str:
        return self.local.model_name

    @property
    def language(self) -> str:
        return self.local.language

    @property
    def session(self):
        if self._session is None:
            import requests

            self._session = requests.Session()
        return self._session

    def _key(self) -> str:
        if self.engine is None:
            return ""
        key = self.environ.get(KEY_ENV[self.engine], "")
        if not key and not self._warned_key:
            self._warned_key = True  # once, not on every turn
            log.warning("[stt] engine = %r but %s is not set; using local Whisper",
                        self.engine, KEY_ENV[self.engine])
        return key

    def keyterms(self) -> list[str]:
        """The vocabulary as separate terms (Deepgram keyterm prompting):
        the English prompt's items and the hotwords."""
        text = ", ".join(p for p in (self.local.initial_prompt if self.language == "en"
                                     else "", self.local.hotwords) if p)
        terms = [t.strip() for t in re.split(r"[,，、;\n]", text) if t.strip()]
        return list(dict.fromkeys(t[:50] for t in terms))[:MAX_KEYTERMS]

    def prompt(self) -> str:
        return ", ".join(p for p in (self.local.initial_prompt, self.local.hotwords) if p)

    def _openai(self, wav: bytes, key: str) -> str:
        data = {"model": self.cloud_model, "language": self.language,
                "response_format": "json"}
        if self.prompt():
            data["prompt"] = self.prompt()
        r = self.session.post(OPENAI_URL, headers={"Authorization": f"Bearer {key}"},
                              data=data, files={"file": ("speech.wav", wav, "audio/wav")},
                              timeout=self.timeout)
        if r.status_code != 200:
            raise CloudSTTError(f"HTTP {r.status_code}")
        text = r.json().get("text")
        if not isinstance(text, str):
            raise CloudSTTError("no text in the reply")
        return text

    def _deepgram(self, wav: bytes, key: str) -> str:
        params = [("model", self.cloud_model), ("language", self.language),
                  ("smart_format", "true")]
        params += [("keyterm", t) for t in self.keyterms()]
        r = self.session.post(DEEPGRAM_URL, params=params, data=wav,
                              headers={"Authorization": f"Token {key}",
                                       "Content-Type": "audio/wav"},
                              timeout=self.timeout)
        if r.status_code != 200:
            raise CloudSTTError(f"HTTP {r.status_code}")
        try:
            text = r.json()["results"]["channels"][0]["alternatives"][0]["transcript"]
        except (KeyError, IndexError, TypeError):
            raise CloudSTTError("no transcript in the reply") from None
        if not isinstance(text, str):
            raise CloudSTTError("no transcript in the reply")
        return text

    def _cloud(self, samples: np.ndarray, key: str) -> str:
        buf = io.BytesIO()
        audio.write_wav(buf, samples)
        call = self._openai if self.engine == "openai" else self._deepgram
        return call(buf.getvalue(), key).strip()

    def transcribe(self, samples: np.ndarray) -> str:
        if samples is None or len(samples) == 0:
            return ""
        seconds = len(samples) / audio.SAMPLE_RATE
        key = self._key()
        if key and seconds <= MAX_SECONDS and self.clock() >= self._down_until:
            started = self.clock()
            try:
                text = self._cloud(samples.astype(np.float32), key)
            except Exception as e:
                # fixed words only: a requests error quotes the URL, whose
                # query holds the keyterms (names)
                why = str(e) if isinstance(e, CloudSTTError) else type(e).__name__
                log.warning("Cloud speech-to-text (%s) failed (%s); using local "
                            "Whisper for %d s", self.engine, why, self.cooldown)
                self._down_until = self.clock() + self.cooldown
            else:
                if self.local.is_echo(text):
                    text = ""
                log.info("Cloud speech-to-text (%s %s): %.1f s of audio -> %d chars "
                         "in %.2f s", self.engine, self.cloud_model, seconds, len(text),
                         self.clock() - started)
                log.debug("Cloud speech-to-text heard: %r", text)
                return text
        return self.local.transcribe(samples)
