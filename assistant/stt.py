"""Speech to text: push-to-talk recording and faster-whisper transcription."""

from __future__ import annotations

import logging
import re

import numpy as np

from assistant.audio import SAMPLE_RATE

log = logging.getLogger(__name__)


class Transcriber:
    """Lazily loads a faster-whisper model and turns 16 kHz audio into text."""

    def __init__(self, model_name: str = "base.en", device: str = "cpu",
                 compute_type: str = "int8", model=None):
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self._model = model

    @property
    def model(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            log.info("Loading Whisper model %s (first run downloads it)...",
                     self.model_name)
            self._model = WhisperModel(self.model_name, device=self.device,
                                       compute_type=self.compute_type)
        return self._model

    def transcribe(self, audio: np.ndarray) -> str:
        if audio is None or len(audio) == 0:
            return ""
        segments, _ = self.model.transcribe(audio.astype(np.float32),
                                            language="en", beam_size=1,
                                            vad_filter=False)
        return " ".join(s.text.strip() for s in segments).strip()


def record_push_to_talk(input_fn=input, device=None) -> np.ndarray | None:
    """Record between two Enter presses from `device` (None: the default
    mic; see audio.resolve_mic_device)."""
    import sounddevice as sd

    input_fn("\n[Press Enter to start talking]")
    frames = []

    def callback(indata, frame_count, time_info, status):
        frames.append(indata.copy())

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                        device=device, callback=callback):
        input_fn("[Recording... press Enter to stop]")
    if not frames:
        return None
    return np.concatenate(frames, axis=0).flatten()


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def rough_match(expected: str, actual: str, threshold: float = 0.6) -> bool:
    """True if most of the expected words appear in the transcript."""
    exp, act = words(expected), set(words(actual))
    if not exp:
        return not act
    return sum(w in act for w in exp) / len(exp) >= threshold
