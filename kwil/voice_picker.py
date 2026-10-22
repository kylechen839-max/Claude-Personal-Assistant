"""Each person's reply voice in the phone app ([tts] picker).

The owner, and each family member on the cloud server, picks the voice the
server reads their replies with, from the voices this server really has:

  piper:<name>     a Piper voice in [tts] voices_dir (<name>.onnx + .onnx.json)
  kokoro:<name>    a Kokoro voice, if Kokoro is set up (never with the server
                   profile: it needs more memory than the cloud box has)
  system           the OS voice (pyttsx3) or espeak-ng: [tts] fallback
  elevenlabs:<id>  an ElevenLabs voice, only with ELEVENLABS_API_KEY set
                   (paid: family-tier members get them only with
                   [tts] family_paid_voices, beta testers never); listed
                   from the API (cached), or any voice ID typed in
  ""               the configured default ([tts] engine)

Only voices in the person's language are offered (zh_CN Piper voices to a
Chinese speaker); ElevenLabs voices are multilingual, so everyone sees them.

The choice is kept per person: the owner's in [tts] voice_file, a family
member's in <family dir>/<id>/voice.json (removed with their folder), each
written privately and atomically. A voice that is gone (its file deleted,
the key removed) falls back to the default, with one log line.

An engine is built once per voice and kept (the last few used), so a
choice doesn't reload a model on every reply; Kokoro voices share the one
loaded model.

HTTP (webapp.create_app; signed-in sessions, the web app's usual same-site
and size checks; on the cloud CloudGuard's sign-in check):
  GET  /api/voices          {voices, current, language, custom}
  POST /api/voices          {voice} -> this person's choice ("" = default)
  POST /api/voices/preview  {voice} -> audio/wav of a short sample sentence
(/api/voice is the voice conversation's WebSocket, hence the plural.)
"""

# No `from __future__ import annotations`: FastAPI must see the real
# Request class in the route signatures (imported inside register()).
import importlib.util
import json
import logging
import os
import re
import shutil
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future
from pathlib import Path

log = logging.getLogger(__name__)

SAMPLES = {"en": "Hi! This is how I'll sound when I read your replies.",
           "zh": "你好！我会用这个声音朗读你的回复。"}
MAX_ENGINES = 4        # voices kept loaded besides the default
MAX_PREVIEWS = 8       # sample clips kept (ElevenLabs ones cost money)
MAX_PIPER = 50         # voice files looked at in voices_dir
MAX_ELEVENLABS = 100   # voices taken from the ElevenLabs list
MAX_ID = 120
ELEVENLABS_VOICES = "https://api.elevenlabs.io/v1/voices"
LIST_TTL = 3600        # seconds the ElevenLabs list is kept
ERROR_TTL = 300        # ... and how long a failed listing waits to retry
FAILED_TTL = 300       # a chosen voice that failed is skipped this long
PREVIEWS_PER_MINUTE = 10  # per browser session (and one at a time)
MAX_TRACKED = 200      # sessions whose preview rate is tracked
MULTI = "multi"        # a voice that speaks every language
FILE = "voice.json"    # in a family member's own folder
_ELEVEN_ID = re.compile(r"^[A-Za-z0-9]{8,64}$")
_KOKORO_NAME = re.compile(r"^[a-z]{2}_[a-z0-9]{1,30}$")


class UnknownVoice(ValueError):
    """Not a voice this server has (for this person)."""


class VoiceCheckFailed(RuntimeError):
    """ElevenLabs couldn't be asked whether a typed-in voice exists."""


class TooManyPreviews(RuntimeError):
    """This session already has a preview rendering, or asked too often."""


def language_of(language) -> str:
    return "zh" if str(language or "en").lower().startswith("zh") else "en"


def _clean(text, limit: int = 60) -> str:
    text = re.sub(r"\s+", " ", str(text or ""))
    return "".join(c for c in text if c.isprintable()).strip()[:limit]


def describe(tts) -> str:
    """A short name for the configured default engine."""
    from kwil.tts import EspeakTTS, PiperTTS, PrintTTS, Pyttsx3TTS
    from kwil.tts_elevenlabs import ElevenLabsTTS
    from kwil.tts_kokoro import KokoroTTS

    if isinstance(tts, KokoroTTS):
        return f"Kokoro {tts.voice}"
    if isinstance(tts, PiperTTS):
        return tts.voice_path.stem
    if isinstance(tts, ElevenLabsTTS):
        return "ElevenLabs"
    if isinstance(tts, (Pyttsx3TTS, EspeakTTS)):
        return "system voice"
    if isinstance(tts, PrintTTS):
        return "no voice"
    return type(tts).__name__


class Catalog:
    """The voices this server can speak with."""

    def __init__(self, config: dict, environ=os.environ, session=None,
                 clock=time.monotonic, family=None):
        from kwil import tts_kokoro

        t = config["tts"]
        self.cfg = t
        self.server = config.get("profile") == "server"
        self.voices_dir = Path(t.get("voices_dir") or "voices")
        self.family_paid = bool(t.get("family_paid_voices"))
        self.family = family  # Family: whose tier may spend on ElevenLabs
        self.kokoro_model = Path(t.get("kokoro_model") or (
            tts_kokoro.DEFAULT_DIR / tts_kokoro.VARIANTS["int8"]))
        self.kokoro_voices = Path(t.get("kokoro_voices") or (
            tts_kokoro.DEFAULT_DIR / tts_kokoro.VOICES_FILE))
        self.environ = environ
        self._session = session  # a requests.Session (a fake in tests)
        self.clock = clock
        self._lock = threading.Lock()
        self._eleven: tuple[float, list[dict]] = (0.0, [])
        self._typed: dict[str, dict] = {}  # typed-in IDs ElevenLabs confirmed
        self._kokoro_names: list[str] | None = None

    # --- Piper ---
    def _piper(self, name: str) -> dict | None:
        from kwil.tts import _VOICE_NAME, piper_voice_problem

        m = _VOICE_NAME.match(name or "")  # also keeps "/" and ".." out
        if not m or piper_voice_problem(self.voices_dir / f"{name}.onnx") is not None:
            return None
        lang, region, speaker, quality = m.group("lang", "region", "name", "quality")
        return {"id": f"piper:{name}", "engine": "piper", "lang": lang, "paid": False,
                "name": f"{speaker.replace('_', ' ').title()} ({lang}_{region}, {quality})"}

    def piper(self) -> list[dict]:
        try:
            files = sorted(self.voices_dir.glob("*.onnx"))[:MAX_PIPER]
        except OSError:
            return []
        return [e for f in files if (e := self._piper(f.stem))]

    # --- Kokoro ---
    def _kokoro_ready(self) -> bool:
        from kwil import tts_kokoro

        return not self.server and tts_kokoro.kokoro_problem(
            self.kokoro_model, self.kokoro_voices) is None

    def _kokoro(self, name: str) -> dict | None:
        from kwil import tts_kokoro

        lang = tts_kokoro.LANGS.get(name[:1]) if _KOKORO_NAME.match(name or "") else None
        if lang is None:  # e.g. Chinese and Japanese, which need extra packages
            return None
        gender = {"f": "female", "m": "male"}.get(name[1], "")
        return {"id": f"kokoro:{name}", "engine": "kokoro", "lang": lang[:2],
                "paid": False,
                "name": f"{name[3:].title()} (Kokoro, {lang}, {gender})"}

    def kokoro_names(self) -> list[str]:
        from kwil import tts_kokoro

        if self._kokoro_names is None:
            self._kokoro_names = tts_kokoro.voice_names(self.kokoro_voices)
        return self._kokoro_names

    def kokoro(self) -> list[dict]:
        if not self._kokoro_ready():
            return []
        return [e for n in self.kokoro_names() if (e := self._kokoro(n))]

    # --- the OS voice ---
    def system(self) -> list[dict]:
        fallback = (self.cfg.get("fallback") or "pyttsx3").lower()
        if fallback == "espeak":
            ok = bool(shutil.which("espeak-ng") or shutil.which("espeak"))
        elif fallback == "pyttsx3":
            ok = importlib.util.find_spec("pyttsx3") is not None
        else:  # "print": no audio at all
            ok = False
        if not ok:
            return []
        return [{"id": "system", "engine": fallback, "lang": "en", "paid": False,
                 "name": "System voice"}]

    # --- ElevenLabs (paid) ---
    def elevenlabs_key(self) -> str:
        from kwil import tts_elevenlabs

        return (self.environ.get(tts_elevenlabs.KEY_ENV) or "").strip()

    def paid_ok(self, user) -> bool:
        """May this person use (and spend money on) ElevenLabs voices? The
        owner always; an invited person only with [tts] family_paid_voices
        and only in the family tier (Family.tier: someone unknown or not
        listed counts as beta), never a beta tester on the owner's bill."""
        if not self.elevenlabs_key():
            return False
        if user is None:
            return True
        return (self.family_paid and self.family is not None
                and self.family.tier(user) == "family")

    @property
    def session(self):
        if self._session is None:
            import requests

            self._session = requests.Session()
        return self._session

    def elevenlabs(self) -> list[dict]:
        key = self.elevenlabs_key()
        if not key:
            return []
        with self._lock:
            expires, voices = self._eleven
            if self.clock() < expires:
                return voices
        voices, ttl = self._list_elevenlabs(key)
        with self._lock:
            self._eleven = (self.clock() + ttl, voices)
        return voices

    def _list_elevenlabs(self, key: str) -> tuple[list[dict], float]:
        try:
            r = self.session.get(ELEVENLABS_VOICES, timeout=8,
                                 headers={"xi-api-key": key, "accept": "application/json"})
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            data = r.json()
        except Exception as e:
            # type and message only: never the request, which holds the key
            log.warning("Listing ElevenLabs voices failed (%s: %s)", type(e).__name__, e)
            return [], ERROR_TTL
        voices = []
        for v in (data.get("voices") or [] if isinstance(data, dict) else []):
            if len(voices) >= MAX_ELEVENLABS:
                break
            vid = v.get("voice_id") if isinstance(v, dict) else None
            if not isinstance(vid, str) or not _ELEVEN_ID.match(vid):
                continue
            voices.append(self._eleven_entry(vid, _clean(v.get("name")) or vid))
        log.info("ElevenLabs: %d voices listed", len(voices))
        return voices, LIST_TTL

    def check_elevenlabs(self, vid: str) -> dict:
        """A typed-in voice ID, checked once with GET /v1/voices/{id} (and
        remembered with its name). UnknownVoice if ElevenLabs doesn't have
        it; VoiceCheckFailed if it couldn't be asked."""
        key = self.elevenlabs_key()
        if not key or not _ELEVEN_ID.match(vid or ""):
            raise UnknownVoice(vid)
        voice = f"elevenlabs:{vid}"
        with self._lock:
            known = {v["id"]: v for v in self._eleven[1] + list(self._typed.values())}
        if voice in known:
            return known[voice]
        try:
            r = self.session.get(f"{ELEVENLABS_VOICES}/{vid}", timeout=8,
                                 headers={"xi-api-key": key, "accept": "application/json"})
            status = r.status_code
            data = r.json() if status == 200 else {}
        except Exception as e:  # type and message only, never the key
            log.warning("Checking an ElevenLabs voice failed (%s: %s)", type(e).__name__, e)
            raise VoiceCheckFailed(vid) from None
        if status in (400, 404, 422):
            raise UnknownVoice(vid)
        if status != 200 or not isinstance(data, dict) or data.get("voice_id") != vid:
            log.warning("Checking an ElevenLabs voice failed (HTTP %s)", status)
            raise VoiceCheckFailed(vid)
        entry = self._eleven_entry(vid, _clean(data.get("name")) or vid)
        with self._lock:
            self._typed[voice] = entry
            while len(self._typed) > 20:
                self._typed.pop(next(iter(self._typed)))
        return entry

    @staticmethod
    def _eleven_entry(vid: str, name: str) -> dict:
        return {"id": f"elevenlabs:{vid}", "engine": "elevenlabs", "lang": MULTI,
                "paid": True, "name": f"{name} (ElevenLabs)"}

    # --- for one person ---
    def voices(self, user, language) -> list[dict]:
        """The voices this person may pick, in their language."""
        lang = language_of(language)
        found = self.piper() + self.kokoro() + self.system()
        if self.paid_ok(user):
            found += self.elevenlabs()
        return [v for v in found if v["lang"] in (lang, MULTI)]

    def find(self, voice: str, user, language) -> dict | None:
        """The voice `voice` if this person may use it here, else None.
        Cheap (no listing, no network): it runs for every reply."""
        if not isinstance(voice, str) or len(voice) > MAX_ID:
            return None
        kind, _, name = voice.partition(":")
        entry = None
        if kind == "piper":
            entry = self._piper(name)
        elif kind == "kokoro":
            if self._kokoro_ready() and name in self.kokoro_names():
                entry = self._kokoro(name)
        elif voice == "system":
            entry = next(iter(self.system()), None)
        elif kind == "elevenlabs" and _ELEVEN_ID.match(name) and self.paid_ok(user):
            with self._lock:
                listed = {v["id"]: v for v in self._eleven[1]}
                listed.update(self._typed)
            # a typed-in ID works too: it's the owner's (or allowed) account
            entry = listed.get(voice) or self._eleven_entry(name, f"Voice {name}")
        if entry is None or entry["lang"] not in (language_of(language), MULTI):
            return None
        return entry


class VoicePrefs:
    """Who picked which voice. The owner's file is [tts] voice_file ("" =
    kept in memory only); a family member's is in their own folder, written
    only while they are listed (so a removal can't race a write back)."""

    MAX_CACHED = 64

    def __init__(self, owner_file, family=None):
        self.owner_file = Path(owner_file) if owner_file else None
        self.family = family
        self._cache: dict = {}
        self._lock = threading.Lock()

    def _path(self, user) -> Path | None:
        if user is None:
            return self.owner_file
        return self.family.dir / user / FILE

    def _listed(self, user) -> bool:
        if user is None:
            return True
        from kwil.family import valid_id

        return (self.family is not None and valid_id(user)
                and self.family.get(user) is not None)

    def get(self, user) -> str:
        """Their voice id ("" = the default)."""
        if not self._listed(user):
            with self._lock:
                self._cache.pop(user, None)
            return ""
        with self._lock:
            if user in self._cache:
                return self._cache[user]
        voice = _read(self._path(user))
        with self._lock:
            if len(self._cache) >= self.MAX_CACHED:
                self._cache.clear()
            self._cache[user] = voice
        return voice

    def set(self, user, voice: str) -> None:
        """Save it ("" deletes the file). LookupError: not (any more) a
        family member. OSError: couldn't write; nothing changed."""
        if user is None:
            self._write(self.owner_file, voice)
        else:
            with self.family.lock:  # family.remove takes it too
                if not self._listed(user):
                    raise LookupError(user)
                self._write(self._path(user), voice)
        with self._lock:
            self._cache[user] = voice

    @staticmethod
    def _write(path: Path | None, voice: str) -> None:
        from kwil.tools.google_auth import write_private

        if path is None:
            return
        if not voice:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        write_private(path, json.dumps({"voice": voice}))


def _read(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ""
    except Exception as e:
        log.warning("Ignoring unreadable %s (%s)", path, type(e).__name__)
        return ""
    voice = data.get("voice") if isinstance(data, dict) else None
    return voice if isinstance(voice, str) and len(voice) <= MAX_ID else ""


class VoiceEngines:
    """One TTS engine per voice id, built on first use and kept (the
    MAX_ENGINES used last); "" is the default engine."""

    def __init__(self, config: dict, default, max_engines: int = MAX_ENGINES):
        self.cfg = dict(config["tts"])
        self.default = default
        self.voices_dir = Path(self.cfg.get("voices_dir") or "voices")
        self.max_engines = max_engines
        self._engines: OrderedDict = OrderedDict()
        self._kokoro = None  # the one loaded Kokoro model, if not the default's
        self._lock = threading.Lock()

    def get(self, voice: str):
        if not voice:
            return self.default
        with self._lock:
            engine = self._engines.get(voice)
            if engine is not None:
                self._engines.move_to_end(voice)
                return engine
            engine = self._engines[voice] = self._build(voice)
            while len(self._engines) > self.max_engines:
                self._engines.popitem(last=False)
            return engine

    def cached(self) -> list[str]:
        with self._lock:
            return list(self._engines)

    def evict(self, engine) -> str | None:
        """Drop `engine` (it failed); returns its voice id, if it was ours."""
        with self._lock:
            for voice, e in list(self._engines.items()):
                if e is engine:
                    del self._engines[voice]
                    return voice
        return None

    def _local(self):
        """A local engine (the ElevenLabs voice's fallback)."""
        from kwil.tts_elevenlabs import ElevenLabsTTS

        d = self.default
        return d.fallback if isinstance(d, ElevenLabsTTS) else d

    def _kokoro_base(self, model: Path, voices: Path):
        from kwil.tts_kokoro import KokoroTTS

        for e in (self.default, getattr(self.default, "fallback", None)):
            if isinstance(e, KokoroTTS) and e._shared is None and \
                    e.model_path == model and e.voices_path == voices:
                return e
        if self._kokoro is None:
            self._kokoro = KokoroTTS(model, voices)
        return self._kokoro

    def _build(self, voice: str):
        from kwil import tts, tts_elevenlabs, tts_kokoro

        c = self.cfg
        kind, _, name = voice.partition(":")
        if kind == "piper":
            path = self.voices_dir / f"{name}.onnx"
            for e in (self.default, getattr(self.default, "fallback", None)):
                # the default voice itself: don't load its model a second time
                if isinstance(e, tts.PiperTTS) and _same_file(e.voice_path, path):
                    return e
            return tts.PiperTTS(path)
        if kind == "kokoro":
            model = Path(c.get("kokoro_model") or (
                tts_kokoro.DEFAULT_DIR / tts_kokoro.VARIANTS["int8"]))
            voices = Path(c.get("kokoro_voices") or (
                tts_kokoro.DEFAULT_DIR / tts_kokoro.VOICES_FILE))
            return tts_kokoro.KokoroTTS(model, voices, voice=name,
                                        speed=c.get("speed") or 1.0,
                                        shared=self._kokoro_base(model, voices))
        if voice == "system":
            return tts.make_tts(c.get("fallback") or "pyttsx3", **c)
        if kind == "elevenlabs":
            return tts_elevenlabs.ElevenLabsTTS(
                self._local(), voice_id=name, model=c.get("elevenlabs_model") or "",
                speed=c.get("speed") or 1.0)
        raise UnknownVoice(voice)


def _same_file(a: Path, b: Path) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return False


class VoicePicker:
    """What the web app uses: the catalog, everyone's choices and the
    engines, and the sample clips."""

    def __init__(self, config: dict, default_tts, family=None, environ=os.environ,
                 session=None, clock=time.monotonic):
        self.catalog = Catalog(config, environ, session, clock, family)
        self.prefs = VoicePrefs(config["tts"].get("voice_file"), family)
        self.engines = VoiceEngines(config, default_tts)
        self.default_tts = default_tts
        self.clock = clock
        self._previews: OrderedDict = OrderedDict()
        self._inflight: dict = {}  # preview key -> Future: one render for all
        self._preview_rates: OrderedDict = OrderedDict()  # session -> RateLimit
        self._previewing: set = set()  # sessions with a preview rendering
        self._missing: OrderedDict = OrderedDict()  # (who, voice): logged once
        self._failed: OrderedDict = OrderedDict()  # voice -> skipped until
        self._lock = threading.Lock()

    @staticmethod
    def who(user) -> str:
        return "the owner" if user is None else str(user)

    def voice_of(self, user, language) -> str:
        """This person's voice id, or "" (the default) if they have none or
        theirs isn't available any more."""
        chosen = self.prefs.get(user)
        if not chosen:
            return chosen
        with self._lock:
            until = self._failed.get(chosen)
            if until is not None and self.clock() >= until:
                del self._failed[chosen]
                until = None
        if until is not None:  # it just failed: the default for a while
            return ""
        if self.catalog.find(chosen, user, language) is not None:
            return chosen
        with self._lock:
            first = (user, chosen) not in self._missing
            self._missing[(user, chosen)] = True
            while len(self._missing) > 64:
                self._missing.popitem(last=False)
        if first:
            log.info("Voice %s isn't available for %s any more; using the default",
                     chosen, self.who(user))
        return ""

    def engine_for(self, user, language):
        return self.engines.get(self.voice_of(user, language))

    def failed(self, engine, error: BaseException | None = None) -> None:
        """A chosen voice's engine raised while rendering: drop it and use
        the default for FAILED_TTL seconds (one log line), instead of trying
        it again on every sentence."""
        voice = self.engines.evict(engine)
        if not voice:
            return
        with self._lock:
            first = voice not in self._failed
            self._failed[voice] = self.clock() + FAILED_TTL
            while len(self._failed) > 64:
                self._failed.popitem(last=False)
        if first:
            log.warning("Voice %s failed (%s); using the default for %d s", voice,
                        type(error).__name__ if error else "error", FAILED_TTL)

    def default_entry(self, language) -> dict:
        from kwil.tts_elevenlabs import ElevenLabsTTS

        return {"id": "", "engine": "default", "lang": language_of(language),
                "paid": isinstance(self.default_tts, ElevenLabsTTS)
                and bool(self.catalog.elevenlabs_key()),
                "name": f"Default ({describe(self.default_tts)})"}

    def listing(self, user, language) -> dict:
        voices = [self.default_entry(language)] + self.catalog.voices(user, language)
        current = self.voice_of(user, language)
        if current and all(v["id"] != current for v in voices):
            entry = self.catalog.find(current, user, language)  # a typed-in ID
            if entry is not None:
                voices.append(entry)
        return {"voices": voices, "current": current,
                "language": language_of(language),
                "custom": self.catalog.paid_ok(user)}  # may type an ElevenLabs ID

    def choose(self, user, language, voice: str) -> str:
        if voice and self.catalog.find(voice, user, language) is None:
            raise UnknownVoice(voice)
        if voice.startswith("elevenlabs:"):
            self.catalog.check_elevenlabs(voice.partition(":")[2])  # typed in?
        with self._lock:
            self._failed.pop(voice, None)  # picked again: try it again
        self.prefs.set(user, voice)
        log.info("Voice for %s set to %s", self.who(user), voice or "the default")
        return voice

    def _take_preview_slot(self, caller) -> None:
        """Called with the lock held. TooManyPreviews if this session has
        one rendering or asked PREVIEWS_PER_MINUTE times in the last minute."""
        from kwil.voice_ws import RateLimit

        rate = self._preview_rates.get(caller)
        if rate is None:
            rate = self._preview_rates[caller] = RateLimit(
                PREVIEWS_PER_MINUTE, 60, self.clock)
            while len(self._preview_rates) > MAX_TRACKED:
                self._preview_rates.popitem(last=False)
        if caller in self._previewing or not rate.hit():
            raise TooManyPreviews(caller)
        self._previewing.add(caller)

    def preview(self, user, language, voice: str, render, caller=None) -> bytes:
        """A short sample sentence in `voice`, as WAV bytes; `render(engine,
        text)` does the rendering (the web app's TTS thread). Cached; callers
        asking for the same clip at once share one render. `caller` (the
        session id) may have one preview rendering at a time and
        PREVIEWS_PER_MINUTE a minute (TooManyPreviews), so nobody can keep
        the TTS thread busy or run up the ElevenLabs bill."""
        if voice and self.catalog.find(voice, user, language) is None:
            raise UnknownVoice(voice)
        if voice.startswith("elevenlabs:"):  # a typed-in ID: does it exist?
            self.catalog.check_elevenlabs(voice.partition(":")[2])
        lang = language_of(language)
        key = (voice, lang)
        with self._lock:
            if key in self._previews:
                self._previews.move_to_end(key)
                return self._previews[key]
            if caller is not None:
                self._take_preview_slot(caller)
            future = self._inflight.get(key)
            mine = future is None
            if mine:
                future = self._inflight[key] = Future()
        try:
            if not mine:
                return future.result(timeout=150)
            try:
                data = render(self.engines.get(voice), SAMPLES[lang])
            except BaseException as e:
                future.set_exception(e)
                raise
            with self._lock:
                self._previews[key] = data
                while len(self._previews) > MAX_PREVIEWS:
                    self._previews.popitem(last=False)
            future.set_result(data)
            return data
        finally:
            with self._lock:
                if mine:
                    self._inflight.pop(key, None)
                self._previewing.discard(caller)


def register(app, assistant, error) -> None:
    """The routes above, on a create_app() app (assistant.voices is set)."""
    from fastapi import Depends, Request
    from fastapi.responses import Response
    from starlette.concurrency import run_in_threadpool

    from kwil.webapp import require_session

    picker: VoicePicker = assistant.voices

    async def voice_in(request: Request):
        try:
            body = await request.json()
        except ValueError:
            body = {}
        voice = body.get("voice", "") if isinstance(body, dict) else None
        return voice if isinstance(voice, str) and len(voice) <= MAX_ID else None

    @app.get("/api/voices")
    async def list_voices(session=Depends(require_session)):
        return await run_in_threadpool(picker.listing, session.user, session.language)

    @app.post("/api/voices")
    async def choose_voice(request: Request, session=Depends(require_session)):
        voice = await voice_in(request)
        if voice is None:
            return error("unknown voice", 400)
        try:
            current = await run_in_threadpool(picker.choose, session.user,
                                              session.language, voice)
        except UnknownVoice:
            return error("unknown voice", 400)
        except VoiceCheckFailed:
            return error("couldn't check that voice with ElevenLabs; try again", 503)
        except LookupError:  # removed while asking
            return error("not signed in", 401)
        except OSError as e:
            log.error("Saving a voice choice failed (%s)", type(e).__name__)
            return error("couldn't save the voice; try again", 500)
        return {"ok": True, "current": current}

    @app.post("/api/voices/preview")
    async def preview_voice(request: Request, session=Depends(require_session)):
        voice = await voice_in(request)
        if voice is None:
            return error("unknown voice", 404)
        try:
            data = await run_in_threadpool(picker.preview, session.user,
                                           session.language, voice, assistant.render,
                                           session.sid)
        except UnknownVoice:
            return error("unknown voice", 404)
        except TooManyPreviews:
            return error("too many previews; try again in a minute", 429)
        except VoiceCheckFailed:
            return error("couldn't check that voice with ElevenLabs; try again", 503)
        except Exception:
            log.exception("A voice preview failed")
            return error("could not make audio", 500)
        return Response(data, media_type="audio/wav")
