"""Small audio helpers shared by TTS, STT, and the wake-word pipeline."""

from __future__ import annotations

import threading
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000  # what Whisper and openWakeWord expect


def _wav_target(path):
    """wave.open takes a file name or an open binary file (e.g. BytesIO)."""
    return path if hasattr(path, "read") or hasattr(path, "write") else str(path)


def read_wav(path) -> tuple[np.ndarray, int]:
    """Read a PCM .wav file (path or binary file) as mono float32 in [-1, 1]."""
    with wave.open(_wav_target(path), "rb") as w:
        sr, width, channels = w.getframerate(), w.getsampwidth(), w.getnchannels()
        raw = w.readframes(w.getnframes())
    if width == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    elif width == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128) / 128.0
    else:
        raise ValueError(f"Unsupported sample width: {width}")
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data, sr


def write_wav(path, samples: np.ndarray, sr: int = SAMPLE_RATE) -> None:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(_wav_target(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def resample(samples: np.ndarray, sr: int, target: int = SAMPLE_RATE) -> np.ndarray:
    """Linear-interpolation resampling; good enough for speech."""
    if sr == target or len(samples) == 0:
        return samples.astype(np.float32)
    n = int(round(len(samples) * target / sr))
    x_old = np.linspace(0.0, 1.0, num=len(samples), endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=n, endpoint=False)
    return np.interp(x_new, x_old, samples).astype(np.float32)


def load_audio(path: str | Path) -> np.ndarray:
    """Read any PCM .wav and return 16 kHz mono float32."""
    data, sr = read_wav(path)
    return resample(data, sr)


def silence(seconds: float, sr: int = SAMPLE_RATE) -> np.ndarray:
    return np.zeros(int(seconds * sr), dtype=np.float32)


def make_chime(sr: int = SAMPLE_RATE) -> np.ndarray:
    """The soft two-note "listening" sound (assistant/earcons.py)."""
    from assistant import earcons

    return earcons.listening(sr)


def make_follow_up_chime(sr: int = SAMPLE_RATE) -> np.ndarray:
    """One short, quiet low note: "still listening" (softer than the wake chime)."""
    from assistant import earcons

    return earcons.thinking(sr)


def resolve_mic_device(value):
    """`[wake] mic_device` -> what sounddevice should open: None for the
    default mic ("" or unset), an int index ("0", 0, "3"), or a name
    fragment ("USB PnP"). Index 0 stays 0: it's a real device, not "unset"."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    return int(text) if text.isdigit() else text


def mic_that_opens(value, sd=None) -> dict:
    """The input device sounddevice will actually open for `value`
    (its info dict, with 'index'); raises if it can't be opened."""
    if sd is None:
        import sounddevice as sd
    device = resolve_mic_device(value)
    if device is None:
        return dict(sd.query_devices(kind="input"))
    return dict(sd.query_devices(device, "input"))


# Barge-in (wakeword.BargeInSpeaker): another thread can cut playback short.
_stop = threading.Event()


def stop_playback() -> None:
    """Stop what play() is playing now, and play nothing more until
    allow_playback() (the rest of an interrupted reply stays silent)."""
    _stop.set()


def allow_playback() -> None:
    _stop.clear()


def playback_stopped() -> bool:
    return _stop.is_set()


def output_latency() -> float | None:
    """Output latency (s) of the stream play() used last, None if unknown."""
    try:
        import sounddevice as sd

        latency = sd.get_stream().latency
        return float(latency[1] if isinstance(latency, (tuple, list)) else latency)
    except Exception:  # nothing played yet, or no sounddevice
        return None


def play(samples: np.ndarray, sr: int = SAMPLE_RATE) -> None:
    """Play and wait until done, or until stop_playback() is called."""
    import sounddevice as sd

    if _stop.is_set():
        return
    sd.play(samples, sr)
    if _stop.wait(len(samples) / sr):  # talked over: stop at once
        sd.stop()
        return
    sd.wait()  # the output latency's last few ms
