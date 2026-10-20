"""Voice conversation mode: the turn pipeline behind the phone page's
hands-free voice view. No HTTP here (voice_ws.py carries it over a
WebSocket), so it is easy to test.

One `VoiceConversation` is one ongoing conversation, reached through a
small backend: `WebBackend` (the web app's per-browser session, through
WebAssistant.chat_stream / answer, so voice and push-to-talk share one
conversation) or `ChatBackend` (a plain ClaudeChat). For each utterance the
page sends (16 kHz mono, cut by its voice activity detector):
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

from assistant import audio, i18n
from assistant.cli import (CJK, get_reply, is_exit, loggable_reply,
                           strip_wake_phrase, zh_is_yes)
from assistant.sentences import SentenceSplitter
from assistant.timing import TurnTimer

log = logging.getLogger(__name__)

# (key, label) for TurnTimer, measured from when the server got the utterance.
VOICE_STAGES = (("transcript", "transcript"),
                ("first_text", "first words from Claude"),
                ("first_sentence", "first sentence"),
                ("audio", "first audio sent"),
                ("reply", "whole reply"))

def question_suffix(lang: str | None = None) -> str:
    return i18n.t("say_yes_no", lang)  # " Please say yes or no." in English


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


def fast_wav(tts, text: str) -> bytes | None:
    """`text` as WAV bytes by the quickest route the engine allows, or None
    to use its save_to_file: Piper renders in memory, and the pyttsx3
    engine on Linux is eSpeak run directly (no driver, no temp file)."""
    from assistant.tts import Pyttsx3TTS

    if hasattr(tts, "synthesize"):  # Piper
        samples, sr = tts.synthesize(text)
        return wav_bytes(samples, sr)
    if (isinstance(tts, Pyttsx3TTS) and sys.platform != "win32"
            and shutil.which("espeak-ng")):
        return espeak_wav(text, tts.rate, tts.voice)
    return None


# --- spoken confirmations ------------------------------------------------------

_YES = {"yes", "yeah", "yep", "yup", "yea", "sure", "confirm", "confirmed",
        "correct", "absolutely", "definitely", "affirmative"}
_YES_PHRASES = ("go ahead", "do it", "delete it", "please do", "move it",
                "change it", "open it", "that's right", "thats right")
# Words that may sit around a yes without changing it ("yes please do it").
_FILLER = {"please", "do", "it", "go", "ahead", "delete", "move", "change", "open",
           "that", "that's", "thats", "right", "ok", "okay", "sure", "hey", "claude",
           "the", "event", "one", "now", "thanks", "thank", "you"}


def is_explicit_yes(text: str) -> bool:
    """Only a clear, short yes confirms a spoken question: "yes", "yeah,
    delete it", "go ahead". Anything with other content ("okay, and what
    about Friday?", "yes but not that one", a new question) is a no."""
    if CJK.search(text):
        return zh_is_yes(text)  # "好的", "是的，删吧"; not "是谁", "好，等一下"
    words = re.findall(r"[a-z']+", text.lower())
    if not words or len(words) > 6:
        return False
    phrase = " ".join(words)
    has_yes = bool(set(words) & _YES) or any(p in phrase for p in _YES_PHRASES)
    return has_yes and all(w in _YES or w in _FILLER for w in words)


def decline_message(question: str, lang: str | None = None) -> str:
    return i18n.t("wont_delete" if "delete" in question.lower() or "删除" in question
                  else "wont_do", lang)


# --- backends -------------------------------------------------------------------

class ChatBackend:
    """A plain ClaudeChat. Its tools' confirm callback must be `self.confirm`
    (blocks the turn until the next utterance answers, or `confirm_timeout`)."""

    def __init__(self, chat, confirm_timeout: float = 60):
        self.chat = chat
        self.confirm_timeout = confirm_timeout
        self.conv: "VoiceConversation | None" = None
        self._answered = threading.Event()
        self._yes = False

    def ask(self, text, on_text, on_tool, cancel) -> str:
        return get_reply(self.chat, text, output_fn=lambda m: None,
                         on_text=on_text, cancel=cancel)

    def confirm(self, question: str) -> bool:
        self._answered.clear()
        self._yes = False
        self.conv.on_confirm(question)
        answered = self._answered.wait(self.confirm_timeout)
        if not answered:
            self.conv.on_status(i18n.t("no_answer"))
        return answered and self._yes

    def answer(self, yes: bool) -> bool:
        self._yes = yes
        self._answered.set()
        return True

    def abort(self) -> None:
        self.chat.abort()

    def mark_interrupted(self, spoken: str) -> bool:
        return self.chat.mark_interrupted(spoken)

    def end(self) -> str | None:
        return self.chat.end_conversation()


class WebBackend:
    """The web app's session for this browser (webapp.WebAssistant), so the
    voice view continues the same conversation as push-to-talk. Tool and
    confirmation events of a voice turn come straight to it (chat_stream's
    on_event), so a page reading /api/events can't take them; a
    confirmation is answered with WebAssistant.answer()."""

    def __init__(self, assistant, session):
        self.assistant = assistant
        self.session = session
        self.conv: "VoiceConversation | None" = None

    def ask(self, text, on_text, on_tool, cancel) -> str:
        def on_event(ev):  # this turn's own events, never the page's queue
            kind = ev.get("type")
            if kind == "confirm":
                self.conv.on_confirm(ev["question"])
            elif kind == "tool":
                on_tool(ev["name"])
            elif kind == "status":
                self.conv.on_status(ev.get("text", ""))

        parts = []
        try:
            for delta in self.assistant.chat_stream(self.session, text, cancel=cancel,
                                                    on_event=on_event):
                parts.append(delta)
                on_text(delta)
        except RuntimeError:  # a push-to-talk turn is running in this session
            parts.append(i18n.t("still_working", self.session.language))
            on_text(parts[-1])
        return "".join(parts)

    def answer(self, yes: bool) -> bool:
        return self.assistant.answer(self.session, yes)

    def abort(self) -> None:
        self.session.chat.abort()

    def mark_interrupted(self, spoken: str) -> bool:
        return self.session.chat.mark_interrupted(spoken)

    def end(self) -> str | None:
        return self.assistant._end(self.session)


# --- the conversation ---------------------------------------------------------

@dataclass
class Chunk:
    text: str
    duration: float
    reply: bool = True  # False for a spoken confirmation question or error
    question: bool = False  # a yes/no question (the page reports its end)


@dataclass
class VoiceTurn:
    id: int
    received: float
    samples: np.ndarray | None = None
    stop: threading.Event = field(default_factory=threading.Event)
    sentences: queue.Queue = field(default_factory=queue.Queue)
    chunks: list[Chunk] = field(default_factory=list)
    played: tuple[int, float] | None = None  # from the page at barge-in
    thread: threading.Thread | None = None
    finished: bool = False
    truncated: bool = False
    asked: bool = False  # Claude was asked (so history has this turn)
    timer: TurnTimer | None = None
    splitter: SentenceSplitter = field(default_factory=SentenceSplitter)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def say(self, sentences) -> None:
        for s in sentences:
            if self.timer:
                self.timer.mark("first_sentence")
            self.sentences.put(s)

    def flush(self) -> None:
        with self.lock:
            self.say(self.splitter.flush())

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
    """A spoken yes/no question of one turn. It can only be answered once
    the page has reported that the question finished playing."""
    turn: int
    question: str
    chunk: int | None = None  # index of the question's audio chunk
    played: bool = False
    answering: bool = False


class VoiceConversation:
    """One hands-free conversation. `send(event, audio=None)` delivers an
    event (and, for "audio" events, WAV bytes) to the page; it is called
    from worker threads and must be thread-safe.

    `transcribe(samples)` and `synthesize(text)` are the Whisper and TTS
    functions (the web app passes its shared, locked ones, for the
    session's language). `language` ("en"/"zh"; None = the server's) is
    the one its own sentences are said in ("Please say yes or no").

    Turns run one at a time on one worker thread, so the conversation never
    has two requests in flight. A new utterance cancels the running turn and
    waits for it to finish; only the newest waiting utterance is kept (older
    ones are dropped before they are transcribed)."""

    def __init__(self, backend, transcribe: Callable[[np.ndarray], str],
                 synthesize: Callable[[str], bytes],
                 send: Callable[..., None], *, clock=time.monotonic,
                 idle_timeout: float = 120, max_session: float = 1800,
                 wake_phrase: str | None = "Hey Claude", language: str | None = None):
        self.backend = backend
        self.language = language
        backend.conv = self
        self.transcribe = transcribe
        self.synthesize = synthesize
        self.send = send
        self.clock = clock
        self.idle_timeout = idle_timeout
        self.max_session = max_session
        self.wake_phrase = wake_phrase
        self.lock = threading.RLock()
        self.wake = threading.Condition(self.lock)
        self.current: VoiceTurn | None = None  # the turn being run
        self.waiting: VoiceTurn | None = None  # the next one (newest wins)
        self.pending: PendingConfirm | None = None
        self.worker: threading.Thread | None = None
        self.closed = False
        self.started = clock()
        self.last_activity = self.started
        self.ended_reason: str | None = None
        self.dropped = 0  # utterances superseded before transcription

    # --- from the page ---
    def utterance(self, turn_id: int, samples: np.ndarray) -> None:
        """A finished utterance. Returns at once; turns run on the worker."""
        with self.lock:
            if self.closed:
                return
            self.last_activity = self.clock()
            pending = self.pending
            if pending is not None:
                if pending.answering:
                    log.info("Dropped utterance %s: already answering", turn_id)
                    return
                if not pending.played:
                    # Kyle spoke before hearing the whole question: never a yes.
                    log.info("Utterance %s came before the question was heard; "
                             "treating it as no", turn_id)
                    self._decline(pending, "not heard")
                    return
                pending.answering = True
                threading.Thread(target=self._answer, args=(pending, turn_id, samples),
                                 name="voice-confirm", daemon=True).start()
                return
            if self.waiting is not None:
                self.dropped += 1
                log.info("Dropped utterance %s (superseded by %s) before "
                         "transcribing it", self.waiting.id, turn_id)
            self.waiting = VoiceTurn(turn_id, self.clock(), samples)
            running = self.current
            if running is not None and not running.finished:
                self._cancel(running)  # talked over it without a barge-in message
            if self.worker is None:
                self.worker = threading.Thread(target=self._work, name="voice-turns",
                                               daemon=True)
                self.worker.start()
            self.wake.notify_all()

    def interrupt(self, turn_id: int, played_index: int = 0,
                  played_fraction: float = 0.0) -> None:
        """Barge-in: the page stopped playback of `turn_id` after playing
        `played_index` whole chunks and `played_fraction` of the next."""
        with self.lock:
            self.last_activity = self.clock()
            turn = self.current
            if turn is None or turn.id != turn_id:
                return
            pending = self.pending
            if pending is not None and pending.turn == turn_id:
                if not pending.answering:
                    # Talking over a yes/no question cancels the action.
                    self._decline(pending, "interrupted")
                return
            turn.played = (int(played_index), float(played_fraction))
            sent = len(turn.chunks)  # 0: stopped before any audio (e.g. a pause)
            if turn.finished:
                self._truncate(turn)
            else:
                self._cancel(turn)
        log.info("Voice barge-in on turn %s after %s chunk(s) + %.0f%% (%d sent)", turn_id,
                 played_index, 100 * float(played_fraction), sent)

    def chunk_played(self, turn_id: int, index: int) -> None:
        """The page finished playing chunk `index` of `turn_id` (it reports
        this for question chunks)."""
        with self.lock:
            pending = self.pending
            if pending is not None and pending.turn == turn_id and \
                    pending.chunk is not None and int(index) == pending.chunk:
                pending.played = True
                self.last_activity = self.clock()

    def expired(self) -> str | None:
        """"idle" or "max_length" when the conversation should end."""
        now = self.clock()
        with self.lock:
            if self.max_session and now - self.started > self.max_session:
                return "max_length"
            busy = (self.current is not None and not self.current.finished) or \
                self.pending is not None or self.waiting is not None
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
            self.waiting = None
            turn = self.current
            if turn is not None and not turn.finished:
                self._cancel(turn)
            if self.pending is not None:
                self.pending = None
                self.backend.answer(False)  # unanswered -> no
            self.wake.notify_all()
            worker = self.worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=15)
        summary = None
        try:
            summary = self.backend.end()
            if summary:
                log.info("Voice conversation saved to memory (%d chars)", len(summary))
                log.debug("Voice conversation summary: %s", summary)
        except Exception:
            log.exception("Ending the voice conversation failed")
        self._send({"type": "ended", "reason": reason, "saved": bool(summary)})
        return summary

    # --- from the backend (the worker thread, or the web events watcher) ---
    def on_confirm(self, question: str) -> None:
        """A tool of the running turn wants a yes: say the question; the next
        utterance after it has been heard answers it."""
        with self.lock:
            turn = self.current
            if turn is None or turn.finished or turn.stop.is_set() or self.closed:
                log.info("Declined a confirmation: its turn was cancelled")
                self.backend.answer(False)
                return
            self.pending = PendingConfirm(turn.id, question)
        turn.flush()  # "Okay." before the question, in order
        self._send({"type": "confirm", "turn": turn.id, "question": question})
        turn.sentences.put(Chunk(question + question_suffix(self.language), 0,
                                 reply=False,
                                 question=True))

    def on_status(self, text: str) -> None:
        with self.lock:
            turn = self.current
            if text == i18n.t("no_answer", self.language):  # the backend gave up waiting
                self.pending = None
        self._send({"type": "status", "turn": turn.id if turn else None, "text": text})

    # --- inside ---
    def _send(self, event: dict, data: bytes | None = None) -> None:
        try:
            self.send(event, data)
        except Exception:  # socket gone: the turn still finishes cleanly
            log.debug("Could not send %s", event.get("type"), exc_info=True)

    def _cancel(self, turn: VoiceTurn) -> None:
        turn.stop.set()
        self.backend.abort()

    def _truncate(self, turn: VoiceTurn) -> None:
        if turn.truncated or not turn.asked:
            return
        turn.truncated = True
        spoken = turn.spoken_text()
        if self.backend.mark_interrupted(spoken):
            log.info("Kept the spoken part of the interrupted reply (%d chars)",
                     len(spoken))

    def _decline(self, pending: PendingConfirm, why: str, text: str = "") -> None:
        """Answer `pending` with no and say so (call with the lock held)."""
        if self.pending is pending:
            self.pending = None
        log.info("Confirmation of turn %s declined (%s)", pending.turn, why)
        self._send({"type": "confirm_answer", "turn": pending.turn, "text": text,
                    "yes": False, "reason": why})
        turn = self.current
        if turn is not None and turn.id == pending.turn and not turn.finished:
            turn.sentences.put(Chunk(decline_message(pending.question, self.language),
                                     0, reply=False))
        self.backend.answer(False)

    def _answer(self, pending: PendingConfirm, utterance_id: int, samples) -> None:
        try:
            text = self.transcribe(samples)
        except Exception:
            log.exception("Transcribing a confirmation failed")
            text = ""
        yes = is_explicit_yes(text)
        log.info("Voice confirmation (%d chars) -> %s", len(text), "yes" if yes else "no")
        log.debug("Voice confirmation text: %r", text)
        with self.lock:
            if self.pending is not pending:
                return  # ended or declined meanwhile
            if not yes:
                self._decline(pending, "not a clear yes", text)
                return
            self.pending = None
            self._send({"type": "confirm_answer", "turn": pending.turn,
                        "utterance": utterance_id, "text": text, "yes": True})
            self.backend.answer(True)

    def _work(self) -> None:
        """Run turns one at a time, in this conversation's language."""
        with i18n.using(self.language):
            self._work_loop()

    def _work_loop(self) -> None:
        while True:
            with self.lock:
                while self.waiting is None and not self.closed:
                    self.wake.wait()
                if self.closed:
                    return
                turn, self.waiting = self.waiting, None
                self.current = turn
            goodbye = self._run_turn(turn)
            if goodbye:
                self.end("goodbye")
                return

    def _speak_worker(self, turn: VoiceTurn) -> None:
        """Synthesize the turn's sentences in order and send each chunk."""
        while True:
            item = turn.sentences.get()
            if item is None:
                return
            chunk = item if isinstance(item, Chunk) else Chunk(item, 0)
            if turn.stop.is_set() and chunk.reply:
                continue  # cancelled: but still say "OK, I won't delete it."
            try:
                data = self.synthesize(chunk.text)
            except Exception:
                log.exception("TTS failed for a %d-character sentence", len(chunk.text))
                continue
            if turn.stop.is_set() and chunk.reply:
                continue
            chunk.duration = wav_duration(data)
            with self.lock:
                index = len(turn.chunks)
                turn.chunks.append(chunk)
                if chunk.question and self.pending is not None and \
                        self.pending.turn == turn.id:
                    self.pending.chunk = index
            if turn.timer:
                turn.timer.mark("audio")
            self._send({"type": "audio", "turn": turn.id, "index": index,
                        "text": chunk.text, "duration": round(chunk.duration, 3),
                        "reply": chunk.reply, "question": chunk.question}, data)

    def _run_turn(self, turn: VoiceTurn) -> bool:
        """One turn, start to finish (on the worker). True after "goodbye"."""
        timer = turn.timer = TurnTimer(self.clock, "utterance received",
                                       at=turn.received, stages=VOICE_STAGES)
        speaker = threading.Thread(target=self._speak_worker, args=(turn,),
                                   name=f"voice-tts-{turn.id}", daemon=True)
        speaker.start()
        goodbye = False
        try:
            goodbye = self._turn_body(turn, timer)
        except Exception:
            log.exception("Voice turn failed")
            message = i18n.t("error", self.language)
            self._send({"type": "error", "turn": turn.id, "message": message})
            turn.sentences.put(Chunk(message, 0, reply=False))
        finally:
            turn.sentences.put(None)
            speaker.join(timeout=120)
            with self.lock:
                turn.finished = True
                if self.pending is not None and self.pending.turn == turn.id:
                    self.pending = None
                if turn.stop.is_set():
                    self._truncate(turn)
                audio_left = sum(c.duration for c in turn.chunks)
                self.last_activity = self.clock() + audio_left
            log.info(timer.summary())
            self._send({"type": "turn_done", "turn": turn.id,
                        "interrupted": turn.stop.is_set(),
                        "chunks": len(turn.chunks), "timings": timer.as_dict(),
                        "summary": timer.summary()})
        return goodbye

    def _turn_body(self, turn: VoiceTurn, timer: TurnTimer) -> bool:
        """Transcribe and answer. True if it was "goodbye"."""
        if turn.stop.is_set():
            return False  # superseded while waiting: skip Whisper entirely
        heard = self.transcribe(turn.samples)
        turn.samples = None
        timer.mark("transcript")
        text = strip_wake_phrase(heard, self.wake_phrase).strip()
        log.info("Voice turn %s: heard %d chars", turn.id, len(heard))
        log.debug("Voice heard: %r", heard)
        self._send({"type": "transcript", "turn": turn.id, "text": heard})
        if not text or turn.stop.is_set():
            return False
        if is_exit(text):
            turn.sentences.put(Chunk(i18n.t("goodbye", self.language), 0, reply=False))
            return True
        streamed = []

        def on_text(delta):
            timer.mark("first_text")
            streamed.append(delta)
            self._send({"type": "text", "turn": turn.id, "delta": delta})
            with turn.lock:
                turn.say(turn.splitter.feed(delta))

        def on_tool(name):
            turn.flush()  # "Let me check." is spoken while the tool runs
            self._send({"type": "tool", "turn": turn.id, "name": name})

        turn.asked = True
        reply = self.backend.ask(text, on_text, on_tool, turn.stop)
        if reply and not streamed:  # an error sentence (nothing was streamed)
            self._send({"type": "error", "turn": turn.id, "message": reply})
            turn.sentences.put(Chunk(reply, 0, reply=False))
        log.info("Voice turn %s: replied %d chars", turn.id, len(reply or ""))
        log.debug("Voice replied: %r", loggable_reply(  # email stays out
            getattr(self.backend, "chat", None)
            or getattr(getattr(self.backend, "session", None), "chat", None), reply))
        turn.flush()
        timer.mark("reply")
        return False
