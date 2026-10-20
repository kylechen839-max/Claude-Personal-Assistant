"""Speech to text: push-to-talk recording and faster-whisper transcription."""

from __future__ import annotations

import logging
import re

import numpy as np

from assistant.audio import SAMPLE_RATE

log = logging.getLogger(__name__)
ZH_PROMPT = "以下是普通话的句子。"


def is_degenerate(text: str) -> bool:
    """Whisper's broken-decode output: three or more characters, all "!"
    ("!!!!", "! ! !"). "!" is token 0, so NaN logits (int8 kernels on some
    CPUs) or a bad sampled fallback come out like this. Whisper's ordinary
    non-speech output ("...", "♪♪♪") is not a failure and isn't retried."""
    chars = "".join(text.split())
    return len(chars) >= 3 and set(chars) == {"!"}


def redecode(model, audio: np.ndarray, fallback=None, what: str = "audio",
             **options) -> list:
    """Decode `audio` again after a degenerate result: greedily (temperature
    0, no sampled fallback, no conditioning on the bad text) and, when
    `fallback` gives one, on a float32 copy of the model. Segments that are
    still degenerate are dropped, so "!!!!" never reaches a transcript.
    Logs counts and lengths only."""
    retry_model, kind = model, "same model"
    if fallback is not None:
        try:
            retry_model = fallback() or model
            kind = "float32" if retry_model is not model else kind
        except Exception:
            log.exception("Loading the float32 Whisper model failed; retrying on the same one")
    log.info("Whisper gave degenerate text for %.1f s of %s; decoding again "
             "greedily (%s)", len(audio) / SAMPLE_RATE, what, kind)
    options = {**options, "temperature": 0.0, "condition_on_previous_text": False}
    segments, _ = retry_model.transcribe(audio, **options)
    segments = list(segments)
    kept = [s for s in segments if not is_degenerate(s.text)]
    if len(kept) < len(segments):
        log.warning("Whisper still degenerate after the retry: dropped %d of %d "
                    "segments of %s", len(segments) - len(kept), len(segments), what)
    else:
        log.info("Whisper retry gave %d segments (%d chars) for %s", len(kept),
                 sum(len(s.text) for s in kept), what)
    return kept


class Transcriber:
    """Lazily loads a faster-whisper model and turns 16 kHz audio into text.

    `language`: "en" (the default) or "zh" (Mandarin; needs a multilingual
    model such as "small", not a ".en" one). The Chinese initial prompt
    nudges Whisper toward Simplified characters and punctuation."""

    def __init__(self, model_name: str = "base.en", device: str = "cpu",
                 compute_type: str = "int8", model=None, language: str = "en"):
        self.language = "zh" if str(language).lower().startswith("zh") else "en"
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self._model = model
        self._own_model = model is None  # loaded here from model_name
        self._float32 = None  # loaded only after a degenerate decode

    @property
    def model(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            log.info("Loading Whisper model %s (first run downloads it)...",
                     self.model_name)
            self._model = WhisperModel(self.model_name, device=self.device,
                                       compute_type=self.compute_type)
        return self._model

    def float32_model(self):
        """A float32 copy of the model for redecode(), or None when this one
        was passed in or is float32 already."""
        if not self._own_model or self.compute_type == "float32":
            return None
        if self._float32 is None:
            from faster_whisper import WhisperModel

            log.info("Loading Whisper model %s in float32 for a retry", self.model_name)
            self._float32 = WhisperModel(self.model_name, device=self.device,
                                         compute_type="float32")
        return self._float32

    def transcribe(self, audio: np.ndarray) -> str:
        if audio is None or len(audio) == 0:
            return ""
        audio = audio.astype(np.float32)
        options = {"initial_prompt": ZH_PROMPT} if self.language == "zh" else {}
        options.update(language=self.language, beam_size=1, vad_filter=False)
        segments, _ = self.model.transcribe(audio, **options)
        segments = list(segments)
        if any(is_degenerate(s.text) for s in segments):
            segments = redecode(self.model, audio, self.float32_model,
                                what="an utterance", **options)
        joiner = "" if self.language == "zh" else " "  # no spaces in Chinese
        return joiner.join(s.text.strip() for s in segments).strip()


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
