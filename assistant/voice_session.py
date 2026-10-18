"""Voice conversation mode: the turn pipeline behind the phone page's
hands-free voice view. No HTTP here (voice_ws.py carries it over a
WebSocket), so it is easy to test.

One `VoiceConversation` is one ongoing ClaudeChat conversation. For each
utterance the page sends (16 kHz mono, cut by its voice activity detector):
Whisper -> Claude's reply streamed -> split into sentences as it arrives ->
each sentence synthesized and sent as its own WAV chunk, so the first
sentence plays while the rest is still being written and rendered.

Barge-in: the page stops playback and reports how much of the reply it
actually played; `interrupt()` aborts the Claude stream and the remaining
TTS, and history keeps only what was said, marked as interrupted.

A tool that needs a yes (deleting an event) asks out loud; the next
utterance answers it and is never sent to Claude as a new request.
"""

from __future__ import annotations

import io
import logging
import queue
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from assistant import audio
from assistant.cli import friendly_error, is_exit, is_yes, strip_wake_phrase
from assistant.sentences import SentenceSplitter
from assistant.timing import TurnTimer

log = logging.getLogger(__name__)

# (key, label) for TurnTimer, measured from when the server got the utterance.
VOICE_STAGES = (("transcript", "transcript"),
                ("first_text", "first words from Claude"),
                ("first_sentence", "first sentence"),
                ("audio", "first audio sent"),
                ("reply", "whole reply"))

QUESTION_SUFFIX = " Please say yes or no."


# --- speech synthesis -----------------------------------------------------------

def wav_bytes(samples: np.ndarray, sr: int) -> bytes:
    buf = io.BytesIO()
    audio.write_wav(buf, samples, sr)
    return buf.getvalue()


def wav_duration(data: bytes) -> float:
    """Seconds of audio in a PCM WAV (0 if it can't be read)."""
    try:
        pos = 12
        rate = channels = bits = 0
        while pos + 8 <= len(data):
            kind, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
            if kind == b"fmt ":
                channels, rate = struct.unpack("<HI", data[pos + 10:pos + 16])
                bits = struct.unpack("<H", data[pos + 22:pos + 24])[0]
            elif kind == b"data":
                size = min(size, len(data) - pos - 8)
                return size / (rate * channels * bits / 8) if rate else 0.0
            pos += 8 + size + (size & 1)
    except struct.error:
        pass
    return 0.0


def fix_stream_wav(data: bytes) -> bytes:
    """espeak-ng --stdout writes placeholder sizes (it streams); browsers'
    decodeAudioData wants real ones."""
    i = data.find(b"data", 12)
    if data[:4] != b"RIFF" or i < 0:
        raise ValueError("espeak-ng did not write a WAV")
    pcm = data[i + 8:]
    pcm = pcm[: len(pcm) - (len(pcm) % 2)]
    return (b"RIFF" + struct.pack("<I", i + 8 + len(pcm) - 8) + data[8:i]
            + b"data" + struct.pack("<I", len(pcm)) + pcm)


_VOICE_NAME = re.compile(r"^[\w+-]{1,40}$")


def espeak_wav(text: str, rate: int = 185, voice: str | None = None,
               timeout: float = 30) -> bytes:
    """Render with the espeak-ng program (what pyttsx3 uses on Linux, minus
    its flaky driver). Text goes in on stdin, so it can't become an option."""
    cmd = ["espeak-ng", "--stdout", "--stdin", "-s", str(int(rate))]
    if voice and _VOICE_NAME.match(voice):
        cmd += ["-v", voice]
    out = subprocess.run(cmd, input=text.encode("utf-8"), capture_output=True,
                         timeout=timeout, check=True).stdout
    return fix_stream_wav(out)


def synthesize_wav(tts, text: str) -> bytes:
    """One sentence as WAV bytes, by the fastest route the engine allows:
    Piper in memory; eSpeak directly on Linux for the pyttsx3 engine; else
    the engine's save_to_file (SAPI on Windows)."""
    from assistant.tts import PrintTTS, Pyttsx3TTS

    if hasattr(tts, "synthesize"):  # Piper
        samples, sr = tts.synthesize(text)
        return wav_bytes(samples, sr)
    if isinstance(tts, PrintTTS):
        return wav_bytes(audio.silence(0.1), audio.SAMPLE_RATE)
    if (isinstance(tts, Pyttsx3TTS) and sys.platform != "win32"
            and shutil.which("espeak-ng")):
        return espeak_wav(text, tts.rate, tts.voice)
    from assistant.webapp import render_wav

    return render_wav(tts, text)


# --- the conversation ---------------------------------------------------------

@dataclass
class Chunk:
    text: str
    duration: float
    reply: bool = True  # False for a spoken confirmation question


@dataclass
class VoiceTurn:
    id: int
    received: float
    stop: threading.Event = field(default_factory=threading.Event)
    sentences: queue.Queue = field(default_factory=queue.Queue)
    chunks: list[Chunk] = field(default_factory=list)
    played: tuple[int, float] | None = None  # from the page at barge-in
    thread: threading.Thread | None = None
    finished: bool = False
    truncated: bool = False
    asked: bool = False  # Claude was asked (so history has this turn)
    timer: TurnTimer | None = None

    def spoken_text(self) -> str:
        """The reply text that was actually played."""
        reply = [c for c in self.chunks if c.reply]
        if self.played is None:  # page didn't say: assume all it was sent
            return " ".join(c.text for c in reply)
        index, fraction = self.played
        index = max(0, min(int(index), len(self.chunks)))
        said = [c.text for c in self.chunks[:index] if c.reply]
        if index < len(self.chunks) and self.chunks[index].reply and fraction > 0:
            words = self.chunks[index].text.split()
            keep = int(len(words) * min(max(float(fraction), 0.0), 1.0))
            if keep:
                said.append(" ".join(words[:keep]) + "...")
        return " ".join(said)


@dataclass
class PendingConfirm:
    turn: int
    question: str
    answered: threading.Event = field(default_factory=threading.Event)
    answer: bool = False


class VoiceConversation:
    """One hands-free conversation. `send(event, audio=None)` delivers an
    event (and, for "audio" events, WAV bytes) to the page; it is called
    from worker threads and must be thread-safe.

    `transcribe(samples)` and `synthesize(text)` are the Whisper and TTS
    functions (the web app passes its shared, locked ones)."""

    def __init__(self, chat, transcribe: Callable[[np.ndarray], str],
                 synthesize: Callable[[str], bytes],
                 send: Callable[..., None], *, clock=time.monotonic,
                 idle_timeout: float = 120, max_session: float = 1800,
                 confirm_timeout: float = 60, wake_phrase: str | None = "Hey Claude"):
        self.chat = chat
        self.transcribe = transcribe
        self.synthesize = synthesize
        self.send = send
        self.clock = clock
        self.idle_timeout = idle_timeout
        self.max_session = max_session
        self.confirm_timeout = confirm_timeout
        self.wake_phrase = wake_phrase
        self.lock = threading.RLock()
        self.current: VoiceTurn | None = None
        self.pending: PendingConfirm | None = None
        self.closed = False
        self.started = clock()
        self.last_activity = self.started
        self.ended_reason: str | None = None

    # --- from the page ---
    def utterance(self, turn_id: int, samples: np.ndarray) -> None:
        """A finished utterance. Returns at once; the turn runs in a thread."""
        with self.lock:
            if self.closed:
                return
            self.last_activity = self.clock()
            pending = self.pending
            if pending is not None and not pending.answered.is_set():
                threading.Thread(target=self._answer, args=(pending, turn_id, samples),
                                 name="voice-confirm", daemon=True).start()
                return
            prev = self.current
            if prev is not None and not prev.finished:
                self._cancel(prev)  # talked over it without a barge-in message
            turn = VoiceTurn(turn_id, self.clock())
            turn.thread = threading.Thread(target=self._run_turn, args=(turn, samples, prev),
                                           name=f"voice-turn-{turn_id}", daemon=True)
            self.current = turn
            turn.thread.start()

    def interrupt(self, turn_id: int, played_index: int = 0,
                  played_fraction: float = 0.0) -> None:
        """Barge-in: the page stopped playback of `turn_id` after playing
        `played_index` whole chunks and `played_fraction` of the next."""
        with self.lock:
            self.last_activity = self.clock()
            turn = self.current
            if turn is None or turn.id != turn_id:
                return
            if self.pending is not None and self.pending.turn == turn_id:
                return  # cut the question short: still waiting for the answer
            turn.played = (int(played_index), float(played_fraction))
            if turn.finished:
                self._truncate(turn)
            else:
                self._cancel(turn)
        log.info("Voice barge-in on turn %s after %s chunk(s) + %.0f%%", turn_id,
                 played_index, 100 * float(played_fraction))

    def expired(self) -> str | None:
        """"idle" or "max_length" when the conversation should end."""
        now = self.clock()
        with self.lock:
            if self.max_session and now - self.started > self.max_session:
                return "max_length"
            busy = (self.current is not None and not self.current.finished) or \
                self.pending is not None
            if not busy and now - self.last_activity > self.idle_timeout:
                return "idle"
        return None

    def end(self, reason: str = "user") -> str | None:
        """Stop everything and save the conversation summary to memory, as
        the voice loop does. Blocks (run it in a worker thread). Returns the
        stored summary, if any."""
        with self.lock:
            if self.closed:
                return None
            self.closed = True
            self.ended_reason = reason
            turn = self.current
            if turn is not None and not turn.finished:
                self._cancel(turn)
            if self.pending is not None:
                self.pending.answered.set()  # unanswered -> no
        if turn is not None and turn.thread is not None and \
                turn.thread is not threading.current_thread():
            turn.thread.join(timeout=15)
        summary = None
        try:
            summary = self.chat.end_conversation()
            if summary:
                log.info("Voice conversation saved to memory: %s", summary)
        except Exception:
            log.exception("Ending the voice conversation failed")
        self._send({"type": "ended", "reason": reason, "saved": bool(summary)})
        return summary

    # --- inside ---
    def _send(self, event: dict, data: bytes | None = None) -> None:
        try:
            self.send(event, data)
        except Exception:  # socket gone: the turn still finishes cleanly
            log.debug("Could not send %s", event.get("type"), exc_info=True)

    def _cancel(self, turn: VoiceTurn) -> None:
        turn.stop.set()
        self.chat.abort()

    def _truncate(self, turn: VoiceTurn) -> None:
        if turn.truncated or not turn.asked:
            return
        turn.truncated = True
        spoken = turn.spoken_text()
        if self.chat.mark_interrupted(spoken):
            log.info("Kept the spoken part of the interrupted reply: %r", spoken)

    def confirm(self, question: str) -> bool:
        """The tools' confirmation callback (runs in the turn's thread): ask
        out loud and wait for the next utterance to answer."""
        with self.lock:
            turn = self.current
            if turn is None or self.closed:
                return False
            pending = PendingConfirm(turn.id, question)
            self.pending = pending
        self._send({"type": "confirm", "turn": turn.id, "question": question})
        turn.sentences.put(Chunk(question + QUESTION_SUFFIX, 0, reply=False))
        answered = pending.answered.wait(self.confirm_timeout)
        with self.lock:
            self.pending = None
            self.last_activity = self.clock()
        if not answered:
            log.info("No answer to %r; treating it as no", question)
            self._send({"type": "status", "turn": turn.id,
                        "text": "No answer, so I didn't do it."})
        return answered and pending.answer and not self.closed

    def _answer(self, pending: PendingConfirm, turn_id: int, samples) -> None:
        try:
            text = self.transcribe(samples)
        except Exception:
            log.exception("Transcribing a confirmation failed")
            text = ""
        yes = is_yes(text)
        log.info("Voice confirmation %r -> %s", text, "yes" if yes else "no")
        self._send({"type": "confirm_answer", "turn": pending.turn,
                    "utterance": turn_id, "text": text, "yes": yes})
        pending.answer = yes
        pending.answered.set()

    def _speak_worker(self, turn: VoiceTurn) -> None:
        """Synthesize the turn's sentences in order and send each chunk."""
        while True:
            item = turn.sentences.get()
            if item is None:
                return
            if turn.stop.is_set():
                continue
            chunk = item if isinstance(item, Chunk) else Chunk(item, 0)
            try:
                data = self.synthesize(chunk.text)
            except Exception:
                log.exception("TTS failed for %r", chunk.text)
                continue
            if turn.stop.is_set():
                continue
            chunk.duration = wav_duration(data)
            with self.lock:
                index = len(turn.chunks)
                turn.chunks.append(chunk)
            if turn.timer:
                turn.timer.mark("audio")
            self._send({"type": "audio", "turn": turn.id, "index": index,
                        "text": chunk.text, "duration": round(chunk.duration, 3),
                        "reply": chunk.reply}, data)

    def _run_turn(self, turn: VoiceTurn, samples, prev: VoiceTurn | None) -> None:
        if prev is not None and prev.thread is not None:
            prev.thread.join(timeout=30)  # history must settle first
        timer = turn.timer = TurnTimer(self.clock, "utterance received",
                                       at=turn.received, stages=VOICE_STAGES)
        speaker = threading.Thread(target=self._speak_worker, args=(turn,),
                                   name=f"voice-tts-{turn.id}", daemon=True)
        speaker.start()
        goodbye = False
        try:
            goodbye = self._turn_body(turn, samples, timer)
        except Exception:
            log.exception("Voice turn failed")
            message = "Sorry, something went wrong."
            self._send({"type": "error", "turn": turn.id, "message": message})
            turn.sentences.put(Chunk(message, 0, reply=False))
        finally:
            turn.sentences.put(None)
            speaker.join(timeout=120)
            with self.lock:
                turn.finished = True
                if turn.stop.is_set():
                    self._truncate(turn)
                audio_left = sum(c.duration for c in turn.chunks)
                self.last_activity = self.clock() + audio_left
            log.info(timer.summary())
            self._send({"type": "turn_done", "turn": turn.id,
                        "interrupted": turn.stop.is_set(),
                        "chunks": len(turn.chunks), "timings": timer.as_dict(),
                        "summary": timer.summary()})
        if goodbye:
            self.end("goodbye")

    def _turn_body(self, turn: VoiceTurn, samples, timer: TurnTimer) -> bool:
        """Transcribe and answer. True if it was "goodbye"."""
        heard = self.transcribe(samples)
        timer.mark("transcript")
        text = strip_wake_phrase(heard, self.wake_phrase).strip()
        log.info("Voice heard: %r", heard)
        self._send({"type": "transcript", "turn": turn.id, "text": heard})
        if not text or turn.stop.is_set():
            return False
        if is_exit(text):
            turn.sentences.put(Chunk("Goodbye!", 0, reply=False))
            return True
        splitter = SentenceSplitter()

        def say(sentences):
            for s in sentences:
                timer.mark("first_sentence")
                turn.sentences.put(s)

        def on_text(delta):
            timer.mark("first_text")
            self._send({"type": "text", "turn": turn.id, "delta": delta})
            say(splitter.feed(delta))

        def on_tool(name):
            self._send({"type": "tool", "turn": turn.id, "name": name})

        turn.asked = True
        try:
            reply, _ = self.chat.ask_stream(
                text, on_text, on_tool=on_tool,
                on_round_end=lambda: say(splitter.flush()), stop=turn.stop)
            log.info("Voice replied: %r", reply)
        except Exception as e:
            turn.asked = False  # ask_stream rolled the turn back
            splitter.flush()
            message = friendly_error(e, text, output_fn=lambda m: None)
            self._send({"type": "error", "turn": turn.id, "message": message})
            turn.sentences.put(Chunk(message, 0, reply=False))
            return False
        say(splitter.flush())
        timer.mark("reply")
        return False
