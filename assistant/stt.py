"""Speech to text: push-to-talk recording and faster-whisper transcription."""

from __future__ import annotations

import logging
import re

import numpy as np

from assistant.audio import SAMPLE_RATE

log = logging.getLogger(__name__)
ZH_PROMPT = "以下是普通话的句子。"
# Words the app hears often, so Whisper spells them right (the initial
# prompt reads to the decoder as text said just before). Names stay out of
# the committed default: put yours in [stt] hotwords.
EN_PROMPT = ("calendar, assignment, Notion, reschedule, tomorrow, remind me, "
             "delete, move, Canvas, exam, quiz")
DEFAULT_PROMPTS = {"en": EN_PROMPT, "zh": ZH_PROMPT}
DEFAULT_BEAM_SIZE = 5  # Whisper's own default; 1 (greedy) mishears more


def is_degenerate(text: str) -> bool:
    """Whisper's broken-decode output: three or more characters, all "!"
    ("!!!!", "! ! !"). "!" is token 0, so NaN logits (int8 kernels on some
    CPUs) or a bad sampled fallback come out like this. Whisper's ordinary
    non-speech output ("...", "♪♪♪") is not a failure and isn't retried."""
    chars = "".join(text.split())
    return len(chars) >= 3 and set(chars) == {"!"}


def redecode(model, audio: np.ndarray, fallback=None, what: str = "audio",
             **options) -> list:
    """Decode `audio` again after a degenerate result: at temperature 0 (no
    sampled fallback, no conditioning on the bad text; the caller's beam
    size, prompt and hotwords are kept) and, when `fallback` gives one, on
    a float32 copy of the model. Segments that are still degenerate are
    dropped, so "!!!!" never reaches a transcript. Logs counts and lengths
    only."""
    retry_model, kind = model, "same model"
    if fallback is not None:
        try:
            retry_model = fallback() or model
            kind = "float32" if retry_model is not model else kind
        except Exception:
            log.exception("Loading the float32 Whisper model failed; retrying on the same one")
    log.info("Whisper gave degenerate text for %.1f s of %s; decoding again "
             "at temperature 0 (%s)", len(audio) / SAMPLE_RATE, what, kind)
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


def _normalized(text: str) -> str:
    """Lower case, with punctuation and runs of spaces as one space."""
    return " ".join(re.sub(r"[\W_]+", " ", text.lower()).split())


def is_prompt_echo(text: str, prompt: str | None, spaced: bool = True,
                   whole_item: bool = True) -> bool:
    """Whisper's known failure on near-silent audio: it "hears" its own
    initial prompt (or hotwords) back. True when `text` is the whole
    prompt, or a piece of it running across three or more of its
    comma-separated items ("assignment, Notion, reschedule"). Shorter
    pieces are real answers and are kept ("tomorrow", "Canvas exam",
    "tomorrow, remind me"), and so is a prompt of one or two items said
    whole unless `whole_item` (a prompt sentence such as the Chinese one;
    not hotwords, names someone may say: "Kyle, Kai"). `spaced`: match
    whole words (False for Chinese)."""
    said, whole = _normalized(text), _normalized(prompt or "")
    if not said or not whole:
        return False
    items = [i for i in (_normalized(i) for i in re.split(r"[,，、;；。.]", prompt or "")) if i]
    if said == whole:
        return whole_item or len(items) >= 3
    pad = " " if spaced else ""
    at = f"{pad}{whole}{pad}".find(f"{pad}{said}{pad}")
    if at < 0:
        return False
    # the items `said` overlaps, by their place in `whole` (items joined
    # by `sep` is `whole`: punctuation normalizes to one space)
    sep = " " if spaced else ""
    start, end, spanned, pos = at, at + len(said), 0, 0
    for item in items:
        found = whole.find(item, pos)
        if found < 0:  # the joined form differs (odd punctuation): be safe
            return False
        if found < end and found + len(item) > start:
            spanned += 1
        pos = found + len(item) + len(sep)
    return spanned >= 3


class Transcriber:
    """Lazily loads a faster-whisper model and turns 16 kHz audio into text.

    `language`: "en" (the default) or "zh" (Mandarin; needs a multilingual
    model such as "small", not a ".en" one). `beam_size`: the beam search
    width (0 or None = the default, 5; 1 = greedy: faster, mishears more).
    `initial_prompt`: None = the language's built-in one (the app's
    vocabulary in English; in Chinese it nudges Whisper toward Simplified
    characters and punctuation), "" = none. `hotwords`: extra words to
    favor, such as names ("" or None = none)."""

    def __init__(self, model_name: str = "base.en", device: str = "cpu",
                 compute_type: str = "int8", model=None, language: str = "en",
                 beam_size: int | None = DEFAULT_BEAM_SIZE,
                 initial_prompt: str | None = None, hotwords: str | None = None):
        self.language = "zh" if str(language).lower().startswith("zh") else "en"
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        # falsy (0, "", None) = the default width, never "unset = greedy"
        self.beam_size = max(1, int(beam_size)) if beam_size else DEFAULT_BEAM_SIZE
        if initial_prompt is None:
            initial_prompt = DEFAULT_PROMPTS[self.language]
        self.initial_prompt = str(initial_prompt).strip()
        self.hotwords = str(hotwords or "").strip()
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

    def options(self) -> dict:
        """Keyword arguments for WhisperModel.transcribe (and redecode)."""
        options = {"language": self.language, "beam_size": self.beam_size,
                   "vad_filter": False}
        if self.initial_prompt:
            options["initial_prompt"] = self.initial_prompt
        if self.hotwords:  # only when set: faster-whisper < 1.0.2 lacks it
            options["hotwords"] = self.hotwords
        return options

    def is_echo(self, text: str) -> bool:
        """`text` is only the prompt or the hotwords said back."""
        spaced = self.language != "zh"
        return (is_prompt_echo(text, self.initial_prompt, spaced)
                or is_prompt_echo(text, self.hotwords, spaced, whole_item=False))

    def transcribe(self, audio: np.ndarray) -> str:
        if audio is None or len(audio) == 0:
            return ""
        audio = audio.astype(np.float32)
        options = self.options()
        segments, _ = self.model.transcribe(audio, **options)
        segments = list(segments)
        if any(is_degenerate(s.text) for s in segments):
            segments = redecode(self.model, audio, self.float32_model,
                                what="an utterance", **options)
        joiner = "" if self.language == "zh" else " "  # no spaces in Chinese
        text = joiner.join(s.text.strip() for s in segments).strip()
        if self.is_echo(text):
            log.info("Whisper only repeated its prompt (%d chars) for %.1f s of "
                     "audio; treated as silence", len(text), len(audio) / SAMPLE_RATE)
            return ""
        log.debug("Whisper heard: %r", text)
        return text


def prompt_setting(value) -> str | None:
    """[stt] initial_prompt: "auto" -> None (the language's built-in
    prompt), "" -> none, anything else as written."""
    if value is None:
        return None
    value = str(value)
    return None if value.strip().lower() == "auto" else value


def from_config(stt: dict, language: str = "en", make=None):
    """The transcriber [stt] asks for: local Whisper, wrapped in the cloud
    engine (stt_cloud.py) when `engine` is "openai" or "deepgram". `make`:
    the local class (default Transcriber)."""
    local = (make or Transcriber)(
        stt["model"], stt["device"], stt["compute_type"], language=language,
        beam_size=stt.get("beam_size"),
        initial_prompt=prompt_setting(stt.get("initial_prompt")),
        hotwords=stt.get("hotwords") or "")
    engine = str(stt.get("engine") or "whisper").strip().lower()
    if engine == "whisper":
        return local
    from assistant.stt_cloud import CloudTranscriber

    return CloudTranscriber(local, engine, model=stt.get("cloud_model") or "",
                            timeout=stt.get("cloud_timeout"))


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
