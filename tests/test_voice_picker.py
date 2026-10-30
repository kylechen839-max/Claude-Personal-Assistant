"""The phone app's reply-voice picker (kwil/voice_picker.py): listing the
voices this server has (Piper files, Kokoro, the OS voice, ElevenLabs with a
mocked API), saving each person's choice (the owner and each family member
apart), the preview clip, falling back on a voice that is gone, engines
cached per voice, and the cloud guard in front of the routes.

All offline: fake voice files in tmp_path, a stand-in `piper` module, a fake
kokoro_onnx and a fake requests session for ElevenLabs."""

import json
import logging
import re
import sys
import threading
import time
import types
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from kwil import audio, cloud, family as family_mod, tts_kokoro, voice_picker, webapp  # noqa: E402
from kwil.chat import ClaudeChat  # noqa: E402
from kwil.config import DEFAULTS, SERVER_PROFILE, load_config  # noqa: E402
from kwil.tts import PiperTTS, PrintTTS  # noqa: E402
from kwil.tts_elevenlabs import ElevenLabsTTS  # noqa: E402
from kwil.tts_kokoro import KokoroTTS  # noqa: E402
from kwil.voice_picker import Catalog, VoicePicker, VoicePrefs  # noqa: E402
from tests.test_voices import fake_kokoro  # noqa: E402,F401  (a fixture)
from tests.test_webapp import BASE, TOKEN, Clock, FakeTranscriber, login  # noqa: E402

KEY = "sk_" + "e" * 40  # a made-up ElevenLabs key
RACHEL, ADAM = "21m00Tcm4TlvDq8ikWAM", "pNInz6obpgDQGcFmaJgB"
# GET /v1/voices, trimmed to the fields that matter (the real shape)
ELEVEN_VOICES = {"voices": [
    {"voice_id": RACHEL, "name": "Rachel", "category": "premade",
     "labels": {"accent": "american", "gender": "female"},
     "preview_url": "https://storage.googleapis.com/x/rachel.mp3",
     "verified_languages": [{"language": "en", "model_id": "eleven_flash_v2_5"}]},
    {"voice_id": ADAM, "name": "Adam\n<b>", "category": "premade", "labels": {}},
    {"voice_id": "../../etc", "name": "bad id"},
    {"name": "no id"},
]}


# --- fakes ---------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else ELEVEN_VOICES

    def json(self):
        return self._body


class FakeSession:
    """requests.Session stand-in for ElevenLabs: `routes` answer by URL
    (GET /v1/voices/{id}), else the list `responses` in turn."""

    def __init__(self, *responses, routes=None):
        self.responses = list(responses) or [FakeResponse()]
        self.routes = routes or {}
        self.calls = []

    def get(self, url, **kw):
        self.calls.append((url, kw))
        if url in self.routes:
            r = self.routes[url]
        else:
            r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(r, Exception):
            raise r
        return r


class Tick:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class FakeEngine:
    """A TTS engine that records what it said (a 440 Hz tone)."""

    def __init__(self, voice, fail=False):
        self.voice, self.fail, self.said = voice, fail, []
        self.on_audio_start = None

    def synthesize(self, text):
        if self.fail:
            raise RuntimeError("voice broke")
        self.said.append(text)
        t = np.arange(2205) / 22050
        return (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), 22050

    def save_to_file(self, text, path):
        samples, sr = self.synthesize(text)
        audio.write_wav(path, samples, sr)


def touch_voice(folder, name, config=True):
    (folder / f"{name}.onnx").write_bytes(b"onnx")
    if config:
        (folder / f"{name}.onnx.json").write_text("{}", encoding="utf-8")


@pytest.fixture
def voices_dir(tmp_path, monkeypatch):
    folder = tmp_path / "voices"
    folder.mkdir()
    for name in ("en_US-lessac-medium", "en_GB-alan-low", "zh_CN-huayan-medium"):
        touch_voice(folder, name)
    touch_voice(folder, "de_DE-thorsten-medium", config=False)  # no .onnx.json
    touch_voice(folder, "my-notes")  # not a voice name
    monkeypatch.setitem(sys.modules, "piper", types.ModuleType("piper"))
    return folder


@pytest.fixture
def config(tmp_path, voices_dir, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    c = load_config(None, None, profile="")
    c["tts"].update(voices_dir=str(voices_dir), voice_file=str(tmp_path / "owner.json"),
                    kokoro_model=str(tmp_path / "none.onnx"),
                    kokoro_voices=str(tmp_path / "none.bin"), fallback="print")
    c["family"].update(enabled=True, dir=str(tmp_path / "users"))
    return c


def ids(voices):
    return [v["id"] for v in voices]


# --- listing -------------------------------------------------------------------

def test_piper_voices_are_listed_by_the_persons_language(config):
    cat = Catalog(config, environ={})
    en = cat.voices(None, "en")
    assert ids(en) == ["piper:en_GB-alan-low", "piper:en_US-lessac-medium"]
    assert en[1]["name"] == "Lessac (en_US, medium)" and en[1]["paid"] is False
    zh = cat.voices("u_someone12", "zh")
    assert ids(zh) == ["piper:zh_CN-huayan-medium"]  # Chinese voices for a zh user
    # a broken or odd file is never offered, nor a path outside voices_dir
    for bad in ("piper:de_DE-thorsten-medium", "piper:my-notes",
                "piper:../voices/en_US-lessac-medium", "piper:", "nope", 42):
        assert cat.find(bad, None, "en") is None


def test_piper_voices_need_the_piper_package(config, monkeypatch):
    monkeypatch.setitem(sys.modules, "piper", None)  # import fails
    assert Catalog(config, environ={}).voices(None, "en") == []


@pytest.fixture
def kokoro_files(tmp_path, config):
    model, voices = tmp_path / "k.onnx", tmp_path / "voices.npz"
    model.write_bytes(b"onnx")
    with open(voices, "wb") as f:  # voices-v1.0.bin is an .npz
        np.savez(f, af_heart=np.zeros(2), bf_emma=np.zeros(2),
                 zf_xiaobei=np.zeros(2), jf_alpha=np.zeros(2))
    config["tts"].update(kokoro_model=str(model), kokoro_voices=str(voices))
    return model, voices


def test_kokoro_voices_when_set_up_and_never_on_the_server(config, kokoro_files,
                                                          fake_kokoro):
    cat = Catalog(config, environ={})
    kokoro = [v for v in cat.voices(None, "en") if v["engine"] == "kokoro"]
    assert ids(kokoro) == ["kokoro:af_heart", "kokoro:bf_emma"]
    assert kokoro[0]["name"] == "Heart (Kokoro, en-us, female)"
    # Chinese/Japanese Kokoro voices need extra packages: not offered
    assert not [v for v in cat.voices(None, "zh") if v["engine"] == "kokoro"]
    assert cat.find("kokoro:zf_xiaobei", None, "zh") is None
    assert fake_kokoro == []  # listing never loads the model
    config["profile"] = "server"  # Kokoro needs more memory than the cloud box
    server = Catalog(config, environ={})
    assert not [v for v in server.voices(None, "en") if v["engine"] == "kokoro"]
    assert server.find("kokoro:af_heart", None, "en") is None


def test_kokoro_voice_names_read_only_the_file_list(kokoro_files, tmp_path, caplog):
    assert tts_kokoro.voice_names(kokoro_files[1]) == [
        "af_heart", "bf_emma", "jf_alpha", "zf_xiaobei"]
    bad = tmp_path / "bad.bin"
    bad.write_bytes(b"not a zip")
    assert tts_kokoro.voice_names(bad) == list(tts_kokoro.ENGLISH_VOICES)


def test_the_system_voice_is_the_configured_fallback(config, monkeypatch):
    assert Catalog(config, environ={}).system() == []  # "print": no audio
    config["tts"]["fallback"] = "espeak"
    monkeypatch.setattr(voice_picker.shutil, "which",
                        lambda name: "/usr/bin/espeak-ng" if name == "espeak-ng" else None)
    cat = Catalog(config, environ={})
    assert "system" in ids(cat.voices(None, "en"))
    assert "system" not in ids(cat.voices(None, "zh"))
    monkeypatch.setattr(voice_picker.shutil, "which", lambda name: None)
    assert Catalog(config, environ={}).find("system", None, "en") is None


def test_elevenlabs_only_with_the_key_listed_from_the_api_and_cached(config, caplog):
    session, clock = FakeSession(), Tick()
    cat = Catalog(config, environ={}, session=session, clock=clock)
    assert not [v for v in cat.voices(None, "en") if v["paid"]]
    assert session.calls == []  # no key: never asked
    cat = Catalog(config, environ={"ELEVENLABS_API_KEY": KEY}, session=session,
                  clock=clock)
    with caplog.at_level(logging.DEBUG):
        paid = [v for v in cat.voices(None, "en") if v["paid"]]
    assert ids(paid) == [f"elevenlabs:{RACHEL}", f"elevenlabs:{ADAM}"]
    assert paid[0]["name"] == "Rachel (ElevenLabs)"
    assert paid[1]["name"] == "Adam <b> (ElevenLabs)"  # one line; the page shows text
    url, kw = session.calls[0]
    assert url == "https://api.elevenlabs.io/v1/voices"
    assert kw["headers"]["xi-api-key"] == KEY and kw["timeout"] > 0
    assert KEY not in caplog.text
    # multilingual: offered to a Chinese speaker too
    assert f"elevenlabs:{RACHEL}" in ids(cat.voices(None, "zh"))
    assert len(session.calls) == 1  # cached
    clock.now += voice_picker.LIST_TTL + 1
    cat.voices(None, "en")
    assert len(session.calls) == 2


def test_an_elevenlabs_failure_lists_none_and_waits_before_retrying(config, caplog):
    session, clock = FakeSession(FakeResponse(401, {"detail": "bad key"}),
                                 FakeResponse()), Tick()
    cat = Catalog(config, environ={"ELEVENLABS_API_KEY": KEY}, session=session,
                  clock=clock)
    with caplog.at_level(logging.WARNING):
        assert not [v for v in cat.voices(None, "en") if v["paid"]]
    assert "HTTP 401" in caplog.text and KEY not in caplog.text
    cat.voices(None, "en")
    assert len(session.calls) == 1
    clock.now += voice_picker.ERROR_TTL + 1
    assert [v for v in cat.voices(None, "en") if v["paid"]]


def test_family_members_get_paid_voices_only_if_allowed(config):
    env = {"ELEVENLABS_API_KEY": KEY}
    cat = Catalog(config, environ=env, session=FakeSession())
    member = "u_member1234"
    assert not [v for v in cat.voices(member, "en") if v["paid"]]
    assert cat.find(f"elevenlabs:{RACHEL}", member, "en") is None
    assert cat.find(f"elevenlabs:{RACHEL}", None, "en") is not None
    config["tts"]["family_paid_voices"] = True
    tiers = {member: "family", "u_tester1234": "beta"}
    fam = SimpleNamespace(tier=lambda uid: tiers.get(uid, "beta"))  # Family.tier
    cat = Catalog(config, environ=env, session=FakeSession(), family=fam)
    assert cat.find(f"elevenlabs:{RACHEL}", member, "en") is not None
    # never a beta tester, someone unlisted, or anyone without a Family to ask
    assert cat.find(f"elevenlabs:{RACHEL}", "u_tester1234", "en") is None
    assert cat.find(f"elevenlabs:{RACHEL}", "u_gone123456", "en") is None
    assert Catalog(config, environ=env, session=FakeSession()).find(
        f"elevenlabs:{RACHEL}", member, "en") is None


def test_an_elevenlabs_voice_id_can_be_typed_in(config):
    cat = Catalog(config, environ={"ELEVENLABS_API_KEY": KEY}, session=FakeSession())
    typed = cat.find("elevenlabs:AbCdEf1234567890", None, "en")
    assert typed["paid"] is True and typed["name"] == "Voice AbCdEf1234567890 (ElevenLabs)"
    for bad in ("elevenlabs:short", "elevenlabs:../../v1/x", "elevenlabs:" + "a" * 65):
        assert cat.find(bad, None, "en") is None
    assert Catalog(config, environ={}).find("elevenlabs:AbCdEf1234567890",
                                            None, "en") is None  # no key


# --- engines -------------------------------------------------------------------

def test_engines_are_built_once_per_voice_and_bounded(config):
    default = PrintTTS()
    picker = VoicePicker(config, default, environ={"ELEVENLABS_API_KEY": KEY},
                         session=FakeSession())
    engines = picker.engines
    assert engines.get("") is default
    lessac = engines.get("piper:en_US-lessac-medium")
    assert isinstance(lessac, PiperTTS) and lessac.voice_path.name == "en_US-lessac-medium.onnx"
    assert engines.get("piper:en_US-lessac-medium") is lessac  # no reload per reply
    eleven = engines.get(f"elevenlabs:{RACHEL}")
    assert isinstance(eleven, ElevenLabsTTS) and eleven.voice_id == RACHEL
    assert eleven.fallback is default  # a local voice if the cloud fails
    for name in ("en_GB-alan-low", "zh_CN-huayan-medium", "en_US-a-low", "en_US-b-low"):
        engines.get(f"piper:{name}")
    assert len(engines.cached()) == voice_picker.MAX_ENGINES
    assert "piper:en_US-lessac-medium" not in engines.cached()  # least recently used


def test_kokoro_voices_share_one_loaded_model(config, kokoro_files, fake_kokoro):
    default = KokoroTTS(*kokoro_files)
    picker = VoicePicker(config, default, environ={})
    emma = picker.engines.get("kokoro:bf_emma")
    assert emma._shared is default and emma.voice == "bf_emma"
    default.synthesize("Hi.")
    emma.synthesize("Hello.")
    loads = [c for c in fake_kokoro if c[0] == "load"]
    creates = [c[2] for c in fake_kokoro if c[0] == "create"]
    assert len(loads) == 1 and creates == ["af_heart", "bf_emma"]


def test_kokoro_shared_uses_the_others_model_and_lock(fake_kokoro, tmp_path):
    base = KokoroTTS(tmp_path / "m", tmp_path / "v")
    other = KokoroTTS(tmp_path / "m", tmp_path / "v", voice="bf_emma", shared=base)
    assert other._lock is base._lock
    other.synthesize("One.")
    base.synthesize("Two.")
    assert [c[0] for c in fake_kokoro].count("load") == 1


# --- saving per person ----------------------------------------------------------

class Env(SimpleNamespace):
    def client(self):
        return TestClient(self.app, base_url=BASE)

    def member(self, owner, name, language):
        r = owner.post("/api/invites", json={"name": name, "language": language})
        assert r.status_code == 200, r.text
        c = self.client()
        r = c.post("/api/invite/accept",
                   json={"invite": r.json()["url"].split("#invite=", 1)[1]})
        assert r.status_code == 200, r.text
        return c, self.family.members()[-1]["id"]


@pytest.fixture
def env(fake_client, config, tmp_path, monkeypatch):
    fam = family_mod.Family(config, client=fake_client)
    default = PrintTTS()
    assistant = webapp.WebAssistant(
        lambda reg: ClaudeChat(client=fake_client, model="m", tools=reg),
        FakeTranscriber(), default, clock=Clock(), family=fam)
    picker = VoicePicker(config, default, family=fam,
                         environ={"ELEVENLABS_API_KEY": KEY}, session=FakeSession())
    built = []

    def build(voice):  # no real Piper model: a fake engine per voice
        built.append(voice)
        return FakeEngine(voice)
    monkeypatch.setattr(picker.engines, "_build", build)
    assistant.voices = picker
    app = webapp.create_app(assistant, TOKEN, reap_interval=3600,
                            limiter=webapp.LoginLimiter(per_ip=3, global_delay=0))
    yield Env(app=app, assistant=assistant, family=fam, picker=picker, built=built,
              config=config, tmp=tmp_path, default=default)
    fam.close()


@pytest.fixture
def owner(env):
    c = env.client()
    login(c)
    return c


def test_each_person_has_their_own_voice(env, owner):
    a, a_id = env.member(owner, "Dad", "en")
    b, b_id = env.member(owner, "Mom", "zh")
    assert owner.get("/api/state").json()["voice_picker"] is True
    listing = owner.get("/api/voices").json()
    assert listing["current"] == "" and listing["custom"] is True
    assert listing["voices"][0] == {"id": "", "engine": "default", "lang": "en",
                                    "paid": False, "name": "Default (no voice)"}
    assert f"elevenlabs:{RACHEL}" in ids(listing["voices"])
    zh = b.get("/api/voices").json()
    assert zh["language"] == "zh" and zh["custom"] is False  # no paid voices for family
    assert ids(zh["voices"]) == ["", "piper:zh_CN-huayan-medium"]

    assert owner.post("/api/voices", json={"voice": "piper:en_US-lessac-medium"}).json() \
        == {"ok": True, "current": "piper:en_US-lessac-medium"}
    assert a.post("/api/voices", json={"voice": "piper:en_GB-alan-low"}).status_code == 200
    assert b.post("/api/voices", json={"voice": "piper:zh_CN-huayan-medium"}).status_code == 200
    assert owner.get("/api/voices").json()["current"] == "piper:en_US-lessac-medium"
    assert a.get("/api/voices").json()["current"] == "piper:en_GB-alan-low"
    assert b.get("/api/voices").json()["current"] == "piper:zh_CN-huayan-medium"
    # A changes again: B and the owner keep theirs
    assert a.post("/api/voices", json={"voice": ""}).json()["current"] == ""
    assert b.get("/api/voices").json()["current"] == "piper:zh_CN-huayan-medium"
    assert owner.get("/api/voices").json()["current"] == "piper:en_US-lessac-medium"

    # on disk: the owner's file, each member in their own folder
    users = env.tmp / "users"
    assert json.loads((env.tmp / "owner.json").read_text(encoding="utf-8")) == {
        "voice": "piper:en_US-lessac-medium"}
    assert not (users / a_id / "voice.json").exists()  # back to the default
    assert json.loads((users / b_id / "voice.json").read_text(encoding="utf-8")) == {
        "voice": "piper:zh_CN-huayan-medium"}
    # a fresh picker (a restart) reads the same choices
    again = VoicePrefs(env.config["tts"]["voice_file"], env.family)
    assert again.get(None) == "piper:en_US-lessac-medium"
    assert again.get(b_id) == "piper:zh_CN-huayan-medium" and again.get(a_id) == ""


def test_a_family_member_cant_pick_what_they_arent_offered(env, owner):
    b, _ = env.member(owner, "Mom", "zh")
    for voice in ("piper:en_US-lessac-medium",  # not her language
                  f"elevenlabs:{RACHEL}",        # paid: owner only by default
                  "piper:../../owner", "system"):
        r = b.post("/api/voices", json={"voice": voice})
        assert r.status_code == 400 and r.json() == {"error": "unknown voice"}, voice
    assert b.post("/api/voices", json={"voice": 7}).status_code == 400
    assert b.post("/api/voices", json={"voice": "x" * 500}).status_code == 400
    assert not (env.tmp / "owner.json").exists()


def test_paid_voices_are_family_tier_only_even_when_allowed(env, owner):
    # [tts] family_paid_voices on must not let beta testers spend the
    # owner's ElevenLabs plan: listing, choose and the reply voice all refuse
    env.picker.catalog.family_paid = True
    tester, tester_id = env.member(owner, "Tester", "en")  # invites default to beta
    r = owner.post("/api/invites", json={"name": "Dad", "language": "en",
                                         "tier": "family"})
    dad = env.client()
    assert dad.post("/api/invite/accept", json={
        "invite": r.json()["url"].split("#invite=", 1)[1]}).status_code == 200
    dad_id = env.family.members()[-1]["id"]
    assert env.family.tier(tester_id) == "beta" and env.family.tier(dad_id) == "family"
    rachel = f"elevenlabs:{RACHEL}"

    listing = tester.get("/api/voices").json()
    assert listing["custom"] is False
    assert not [v for v in listing["voices"] if v["paid"]]
    r = tester.post("/api/voices", json={"voice": rachel})
    assert r.status_code == 400 and r.json() == {"error": "unknown voice"}
    assert not (env.tmp / "users" / tester_id / "voice.json").exists()

    # a paid voice saved for them anyway (e.g. while they were family) isn't used
    VoicePrefs(env.config["tts"]["voice_file"], env.family).set(tester_id, rachel)
    assert env.picker.voice_of(tester_id, "en") == ""
    assert env.picker.engine_for(tester_id, "en") is env.default

    listing = dad.get("/api/voices").json()
    assert listing["custom"] is True and rachel in ids(listing["voices"])
    assert dad.post("/api/voices", json={"voice": rachel}).json()["current"] == rachel
    assert env.picker.voice_of(dad_id, "en") == rachel
    assert owner.post("/api/voices", json={"voice": rachel}).json()["current"] == rachel
    assert rachel in ids(owner.get("/api/voices").json()["voices"])



def test_a_paid_server_default_is_never_a_beta_testers_voice(env, owner, monkeypatch):
    """[tts] engine = "elevenlabs" on a server with family on: the owner and
    a family member allowed paid voices hear it; a beta tester, or family
    without family_paid_voices, hears its local fallback (no owner bill)."""
    from kwil.tts_elevenlabs import ElevenLabsTTS

    paid = ElevenLabsTTS(env.default)
    env.assistant.tts = paid
    monkeypatch.setattr(env.picker, "engine_for", lambda user, language: paid)
    tester, tester_id = env.member(owner, "Tester", "en")  # beta
    r = owner.post("/api/invites", json={"name": "Dad", "language": "en", "tier": "family"})
    dad = env.client()
    assert dad.post("/api/invite/accept", json={
        "invite": r.json()["url"].split("#invite=", 1)[1]}).status_code == 200
    dad_id = env.family.members()[-1]["id"]
    for c in (owner, tester, dad):
        c.get("/api/state")
    by_user = {s.user: s for s in env.assistant.sessions.values()}
    assert env.assistant.tts_for(by_user[None]) is paid
    assert env.assistant.tts_for(by_user[tester_id]) is env.default
    env.picker.catalog.family_paid = False
    assert env.assistant.tts_for(by_user[dad_id]) is env.default
    env.picker.catalog.family_paid = True
    assert env.assistant.tts_for(by_user[dad_id]) is paid
    env.picker.catalog.family_paid = True
    assert env.assistant.tts_for(by_user[tester_id]) is env.default  # beta even so

def test_replies_use_the_persons_voice_from_the_next_reply(env, owner):
    a, a_id = env.member(owner, "Dad", "en")
    a.get("/api/state")
    owner_s = next(s for s in env.assistant.sessions.values() if s.user is None)
    a_s = next(s for s in env.assistant.sessions.values() if s.user == a_id)
    assert env.assistant.tts_for(owner_s) is env.default
    owner.post("/api/voices", json={"voice": "piper:en_US-lessac-medium"})
    a.post("/api/voices", json={"voice": "piper:en_GB-alan-low"})
    wav = env.assistant.synthesize("Hello there.", owner_s)
    assert wav[:4] == b"RIFF"
    assert env.assistant.tts_for(owner_s).voice == "piper:en_US-lessac-medium"
    assert env.assistant.tts_for(a_s).voice == "piper:en_GB-alan-low"
    assert env.assistant.tts_for(owner_s).said == ["Hello there."]
    for _ in range(3):
        env.assistant.synthesize("Again.", owner_s)
    assert env.built.count("piper:en_US-lessac-medium") == 1  # built once
    assert env.assistant.tts_for(None) is env.default


def test_reply_audio_renders_in_the_persons_voice(env, owner):
    owner.post("/api/voices", json={"voice": "piper:en_US-lessac-medium"})
    s = next(iter(env.assistant.sessions.values()))
    rid = env.assistant.remember("A reply.", s)
    r = owner.get(f"/api/audio/{rid}")
    assert r.status_code == 200 and r.content[:4] == b"RIFF"
    assert env.assistant.tts_for(s).said == ["A reply."]


def test_a_failing_chosen_voice_falls_back_to_the_default(env, owner, caplog,
                                                          monkeypatch):
    monkeypatch.setattr(env.picker.engines, "_build",
                        lambda voice: FakeEngine(voice, fail=True))
    owner.post("/api/voices", json={"voice": "piper:en_US-lessac-medium"})
    s = next(iter(env.assistant.sessions.values()))
    with caplog.at_level(logging.WARNING):
        wav = env.assistant.synthesize("Private reply text.", s)
    assert wav[:4] == b"RIFF"  # the default (PrintTTS) rendered it
    assert "using the default" in caplog.text
    assert "Private reply text" not in caplog.text


def test_an_unknown_or_removed_voice_falls_back_with_one_log_line(env, owner, caplog):
    owner.post("/api/voices", json={"voice": "piper:en_GB-alan-low"})
    s = next(iter(env.assistant.sessions.values()))
    (env.tmp / "voices" / "en_GB-alan-low.onnx").unlink()  # the voice is removed
    with caplog.at_level(logging.INFO, logger="kwil.voice_picker"):
        for _ in range(3):
            assert env.assistant.tts_for(s) is env.default
    lines = [r for r in caplog.records if "isn't available" in r.getMessage()]
    assert len(lines) == 1 and lines[0].levelno == logging.INFO
    assert "piper:en_GB-alan-low" in lines[0].getMessage()
    assert owner.get("/api/voices").json()["current"] == ""
    # a hand-edited file naming something unknown: the default too
    (env.tmp / "owner.json").write_text('{"voice": "piper:../../../etc/passwd"}', encoding="utf-8")
    fresh = VoicePicker(env.config, env.default)
    assert fresh.voice_of(None, "en") == "" and fresh.engine_for(None, "en") is env.default
    (env.tmp / "owner.json").write_text("not json", encoding="utf-8")
    assert VoicePrefs(env.tmp / "owner.json").get(None) == ""


def test_choosing_logs_ids_only(env, owner, caplog):
    with caplog.at_level(logging.DEBUG):
        owner.post("/api/voices", json={"voice": "piper:en_US-lessac-medium"})
    infos = [r.getMessage() for r in caplog.records
             if r.name == "kwil.voice_picker" and r.levelno == logging.INFO]
    assert infos == ["Voice for the owner set to piper:en_US-lessac-medium"]


def test_preview_returns_audio_cached_and_only_for_offered_voices(env, owner):
    a, _ = env.member(owner, "Dad", "en")
    b, _ = env.member(owner, "Mom", "zh")
    r = owner.post("/api/voices/preview", json={"voice": "piper:en_US-lessac-medium"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    assert r.content[:4] == b"RIFF" and len(r.content) > 44
    engine = env.picker.engines.get("piper:en_US-lessac-medium")
    assert engine.said == [voice_picker.SAMPLES["en"]]
    a.post("/api/voices/preview", json={"voice": "piper:en_US-lessac-medium"})
    assert len(engine.said) == 1  # the clip is reused (ElevenLabs costs money)
    b.post("/api/voices/preview", json={"voice": "piper:zh_CN-huayan-medium"})
    assert env.picker.engines.get("piper:zh_CN-huayan-medium").said == [
        voice_picker.SAMPLES["zh"]]
    default = owner.post("/api/voices/preview", json={"voice": ""})
    assert default.status_code == 200 and default.content[:4] == b"RIFF"
    assert b.post("/api/voices/preview",
                  json={"voice": f"elevenlabs:{RACHEL}"}).status_code == 404
    assert owner.post("/api/voices/preview", json={"voice": "piper:nope"}).status_code == 404
    # previewing doesn't change anyone's choice
    assert owner.get("/api/voices").json()["current"] == ""


def test_a_removed_family_members_choice_goes_with_their_folder(env, owner):
    a, a_id = env.member(owner, "Dad", "en")
    a.post("/api/voices", json={"voice": "piper:en_GB-alan-low"})
    path = env.tmp / "users" / a_id / "voice.json"
    assert path.exists()
    assert owner.post(f"/api/users/{a_id}/remove", json={}).status_code == 200
    assert not path.exists()
    assert env.picker.prefs.get(a_id) == ""
    with pytest.raises(LookupError):  # a late write can't bring it back
        env.picker.prefs.set(a_id, "piper:en_GB-alan-low")
    assert not path.parent.exists()
    assert a.get("/api/voices").status_code == 401


def test_voice_routes_need_a_session_and_the_same_site(env):
    anon = env.client()
    assert anon.get("/api/voices").status_code == 401
    assert anon.post("/api/voices", json={"voice": ""}).status_code == 401
    assert anon.post("/api/voices/preview", json={"voice": ""}).status_code == 401
    c = env.client()
    login(c)
    r = c.post("/api/voices", json={"voice": "piper:en_US-lessac-medium"},
               headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert c.get("/api/voices").json()["current"] == ""


def test_without_the_picker_there_are_no_voice_routes(fake_client):
    assistant = webapp.WebAssistant(
        lambda reg: ClaudeChat(client=fake_client, model="m", tools=reg),
        FakeTranscriber(), PrintTTS(), clock=Clock())
    c = TestClient(webapp.create_app(assistant, TOKEN, reap_interval=3600), base_url=BASE)
    login(c)
    assert c.get("/api/state").json()["voice_picker"] is False
    assert c.get("/api/voices").status_code == 404
    s = next(iter(assistant.sessions.values()))
    assert assistant.tts_for(s) is assistant.tts


def test_build_assistant_adds_the_picker_unless_turned_off(config, monkeypatch):
    import kwil.stt as stt

    monkeypatch.setattr(stt, "Transcriber", lambda *a, **k: FakeTranscriber())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    for section in ("calendar", "gmail", "notion", "memory", "notes", "history",
                    "reminders"):
        config[section]["enabled"] = False
    config["family"]["enabled"] = False
    a = webapp.build_assistant(config, "print", output_fn=lambda m: None)
    assert isinstance(a.voices, VoicePicker) and a.voices.engines.default is a.tts
    config["tts"]["picker"] = False
    assert webapp.build_assistant(config, "print", output_fn=lambda m: None).voices is None


def test_settings_have_defaults_and_the_server_keeps_the_choice_on_its_disk():
    t = DEFAULTS["tts"]
    assert t["picker"] is True and t["voices_dir"] == "voices"
    assert t["voice_file"] == "data/voice_choice.json"
    assert t["family_paid_voices"] is False  # family can't spend on ElevenLabs
    assert SERVER_PROFILE["tts"]["voice_file"].startswith("/data/")
    mom = load_config("config.mom.toml", None, profile="")
    assert mom["tts"]["voice_file"] == "data/mom/voice_choice.json"  # its own instance


# --- the cloud guard -----------------------------------------------------------

PROXY, PUBLIC_IP = "10.1.2.3", "203.0.113.9"
TRUSTED = ["10.0.0.0/8", "127.0.0.0/8"]


def via_proxy(extra=None):
    return {"X-Forwarded-For": PUBLIC_IP, "X-Forwarded-Proto": "https", **(extra or {})}


@pytest.fixture
def cloud_client(env):
    trust = cloud.ProxyNetworks(hops=1, networks=tuple(cloud.parse_networks(TRUSTED)))
    limiter = cloud.BoundedLoginLimiter(clock=Clock())
    app = webapp.create_app(env.assistant, TOKEN, limiter=limiter, trusted_proxy=trust,
                            reap_interval=3600)

    async def slept(seconds):
        pass

    guard = cloud.CloudGuard(app, TOKEN, sleep=slept, trusted_proxies=TRUSTED,
                             max_upload_bytes=200_000, max_body_bytes=16_384,
                             lockout=limiter, limiter=cloud.RateLimiter(clock=Clock()))

    async def at_proxy(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = {**scope, "client": (PROXY, 40000)}
        await guard(scope, receive, send)
    return TestClient(at_proxy, base_url="https://testserver", follow_redirects=False)


def test_the_cloud_guard_lets_only_signed_in_people_at_the_voices(cloud_client):
    c = cloud_client
    assert c.get("/voice-picker.js", headers=via_proxy()).status_code == 200  # page code
    for method, path in (("GET", "/api/voices"), ("POST", "/api/voices"),
                         ("POST", "/api/voices/preview")):
        r = c.request(method, path, json={"voice": ""}, headers=via_proxy())
        assert r.status_code == 401 and r.json() == {"error": "not signed in"}, path
    assert c.post("/api/login", json={"token": TOKEN},
                  headers=via_proxy()).status_code == 200
    assert c.get("/api/voices", headers=via_proxy()).status_code == 200
    r = c.post("/api/voices", json={"voice": "piper:en_US-lessac-medium"},
               headers=via_proxy())
    assert r.status_code == 200, r.text
    r = c.post("/api/voices/preview", json={"voice": ""}, headers=via_proxy())
    assert r.status_code == 200 and r.content[:4] == b"RIFF"
    # the body cap for non-upload routes holds here too
    big = c.post("/api/voices", content=b"{" + b" " * 20_000 + b"}",
                 headers=via_proxy({"Content-Type": "application/json"}))
    assert big.status_code == 413


def test_page_files_list_the_picker():
    from pathlib import Path

    web = Path(webapp.STATIC)
    assert '<script src="/voice-picker.js" defer></script>' in (web / "index.html").read_text(encoding="utf-8")
    assert '"/voice-picker.js"' in (web / "sw.js").read_text(encoding="utf-8")
    assert "/voice-picker.js" in cloud.PUBLIC_GET
    html = (web / "index.html").read_text(encoding="utf-8")
    for el in ("voice-picker", "voice-select", "voice-paid", "voice-preview",
               "voice-custom", "voice-custom-use", "voice-status"):
        assert f'id="{el}"' in html
    i18n = (web / "i18n.js").read_text(encoding="utf-8")
    for text in ("Reply voice", "Preview", "paid", "Use this voice"):
        assert f'"{text}":' in i18n


# --- round 2: judge findings ---------------------------------------------------

def test_new_tests_read_and_write_text_as_utf8():
    """Windows defaults to cp1252: i18n.js (Chinese) must be read as UTF-8."""
    from pathlib import Path

    source = Path(__file__).read_text(encoding="utf-8")
    calls = re.findall(r"\.(?:read|write)_text\((.*)\)", source)
    assert calls and all("encoding=" in c for c in calls), [
        c for c in calls if "encoding=" not in c]


def test_the_default_piper_voice_is_not_loaded_twice(config, voices_dir):
    default = PiperTTS(voices_dir / "en_US-lessac-medium.onnx")
    picker = VoicePicker(config, default, environ={})
    assert picker.engines.get("piper:en_US-lessac-medium") is default
    assert picker.engines.get("piper:en_GB-alan-low") is not default
    # also when the default is ElevenLabs over that Piper voice
    eleven = ElevenLabsTTS(default, voice_id=RACHEL)
    picker = VoicePicker(config, eleven, environ={})
    assert picker.engines.get("piper:en_US-lessac-medium") is default


def test_concurrent_previews_of_one_voice_share_one_render(env):
    started, release, renders = threading.Event(), threading.Event(), []

    def render(engine, text):
        renders.append(text)
        started.set()
        release.wait(5)
        return b"RIFF-shared"

    results = []

    def ask(caller):
        results.append(env.picker.preview(None, "en", "piper:en_GB-alan-low",
                                          render, caller=caller))
    first = threading.Thread(target=ask, args=("sid-a",))
    first.start()
    assert started.wait(5)
    second = threading.Thread(target=ask, args=("sid-b",))
    second.start()
    time.sleep(0.1)  # sid-b is now waiting on sid-a's render
    # sid-a already has one rendering: another of its own is refused
    with pytest.raises(voice_picker.TooManyPreviews):
        env.picker.preview(None, "en", "piper:en_US-lessac-medium", render,
                           caller="sid-a")
    release.set()
    first.join(5), second.join(5)
    assert results == [b"RIFF-shared", b"RIFF-shared"] and len(renders) == 1
    assert env.picker._inflight == {} and env.picker._previewing == set()


def test_a_failed_shared_preview_fails_every_waiter_and_frees_the_slot(env):
    def render(engine, text):
        raise RuntimeError("no audio")

    with pytest.raises(RuntimeError):
        env.picker.preview(None, "en", "", render, caller="sid-a")
    assert env.picker._inflight == {} and env.picker._previewing == set()
    assert env.picker.preview(None, "en", "", lambda e, t: b"RIFF", caller="sid-a") == b"RIFF"


def test_previews_are_limited_per_session(env, owner):
    a, _ = env.member(owner, "Dad", "en")
    for _ in range(voice_picker.PREVIEWS_PER_MINUTE):
        env.picker._previews.clear()  # a new clip each time
        assert owner.post("/api/voices/preview", json={"voice": ""}).status_code == 200
    env.picker._previews.clear()
    r = owner.post("/api/voices/preview", json={"voice": ""})
    assert r.status_code == 429
    assert r.json() == {"error": "too many previews; try again in a minute"}
    # another person isn't held back; a cached clip costs nothing
    assert a.post("/api/voices/preview", json={"voice": ""}).status_code == 200
    assert owner.post("/api/voices/preview", json={"voice": ""}).status_code == 200


def test_the_preview_cache_is_bounded(config, voices_dir):
    names = [f"en_US-v{i}-low" for i in range(voice_picker.MAX_PREVIEWS + 3)]
    for name in names:
        touch_voice(voices_dir, name)
    picker = VoicePicker(config, PrintTTS(), environ={})
    picker.engines._build = lambda voice: FakeEngine(voice)
    for i, name in enumerate(names):
        assert picker.preview(None, "en", f"piper:{name}",
                              lambda e, t, i=i: b"RIFF%d" % i) == b"RIFF%d" % i
    assert len(picker._previews) == voice_picker.MAX_PREVIEWS
    assert list(picker._previews)[0] == (f"piper:{names[3]}", "en")  # oldest out


def test_a_broken_chosen_voice_is_skipped_for_a_while(config, fake_client, caplog):
    clock = Tick()
    default = PrintTTS()
    assistant = webapp.WebAssistant(
        lambda reg: ClaudeChat(client=fake_client, model="m", tools=reg),
        FakeTranscriber(), default, clock=Clock())
    picker = VoicePicker(config, default, environ={}, clock=clock)
    built = []
    picker.engines._build = lambda voice: built.append(voice) or FakeEngine(voice, fail=True)
    assistant.voices = picker
    picker.choose(None, "en", "piper:en_US-lessac-medium")
    s = SimpleNamespace(user=None, language="en")
    with caplog.at_level(logging.INFO):
        for _ in range(4):
            assert assistant.synthesize("A sentence.", s)[:4] == b"RIFF"
    assert built == ["piper:en_US-lessac-medium"]  # tried once, not per sentence
    assert "piper:en_US-lessac-medium" not in picker.engines.cached()
    assert picker.voice_of(None, "en") == ""
    warnings = [r for r in caplog.records if "failed" in r.getMessage()]
    assert len(warnings) == 1 and "A sentence" not in caplog.text
    clock.now += voice_picker.FAILED_TTL + 1  # then it is tried again
    assert picker.voice_of(None, "en") == "piper:en_US-lessac-medium"
    assistant.synthesize("Again.", s)
    assert built.count("piper:en_US-lessac-medium") == 2
    # picking it again right away tries it again too
    picker.choose(None, "en", "piper:en_US-lessac-medium")
    assert picker.voice_of(None, "en") == "piper:en_US-lessac-medium"


TYPED = "AbCdEf1234567890"
VOICE_URL = f"https://api.elevenlabs.io/v1/voices/{TYPED}"


def test_a_typed_elevenlabs_id_is_checked_once_with_elevenlabs(env, owner):
    session = env.picker.catalog._session
    session.routes[VOICE_URL] = FakeResponse(400, {"detail": {
        "status": "voice_not_found", "message": "A voice with the voice_id was not found."}})
    r = owner.post("/api/voices", json={"voice": f"elevenlabs:{TYPED}"})
    assert r.status_code == 400 and r.json() == {"error": "unknown voice"}
    url, kw = session.calls[-1]
    assert url == VOICE_URL and kw["headers"]["xi-api-key"] == KEY
    assert owner.get("/api/voices").json()["current"] == ""
    # (GET /v1/voices/{id}: the voice object, trimmed)
    session.routes[VOICE_URL] = FakeResponse(200, {
        "voice_id": TYPED, "name": "My Clone", "category": "cloned", "labels": {}})
    r = owner.post("/api/voices", json={"voice": f"elevenlabs:{TYPED}"})
    assert r.status_code == 200, r.text
    listing = owner.get("/api/voices").json()
    assert listing["current"] == f"elevenlabs:{TYPED}"
    assert {"id": f"elevenlabs:{TYPED}", "engine": "elevenlabs", "lang": "multi",
            "paid": True, "name": "My Clone (ElevenLabs)"} in listing["voices"]
    checks = [c for c in session.calls if c[0] == VOICE_URL]
    owner.post("/api/voices", json={"voice": f"elevenlabs:{TYPED}"})
    assert len([c for c in session.calls if c[0] == VOICE_URL]) == len(checks)  # once
    # a listed voice needs no check
    owner.post("/api/voices", json={"voice": f"elevenlabs:{RACHEL}"})
    assert not [c for c in session.calls if c[0].endswith(RACHEL)]


def test_a_typed_id_that_cant_be_checked_isnt_saved(env, owner, caplog):
    session = env.picker.catalog._session
    session.routes[VOICE_URL] = ConnectionError("network down")
    with caplog.at_level(logging.WARNING):
        r = owner.post("/api/voices", json={"voice": f"elevenlabs:{TYPED}"})
    assert r.status_code == 503
    assert owner.get("/api/voices").json()["current"] == ""
    assert KEY not in caplog.text


def test_a_second_caller_waiting_on_a_failing_render_gets_the_error_promptly(env):
    started, release = threading.Event(), threading.Event()

    def render(engine, text):
        started.set()
        release.wait(5)
        raise RuntimeError("no audio")

    errors = []

    def ask(caller):
        try:
            env.picker.preview(None, "en", "piper:en_GB-alan-low", render, caller=caller)
        except RuntimeError as e:
            errors.append((caller, str(e), time.monotonic()))

    first = threading.Thread(target=ask, args=("sid-a",), daemon=True)
    first.start()
    assert started.wait(5)
    waiter = threading.Thread(target=ask, args=("sid-b",), daemon=True)
    waiter.start()
    time.sleep(0.1)  # sid-b now waits on sid-a's render
    failed_at = time.monotonic()
    release.set()
    first.join(5), waiter.join(5)
    assert not waiter.is_alive()  # not left waiting for the 150 s timeout
    assert sorted(e[:2] for e in errors) == [("sid-a", "no audio"), ("sid-b", "no audio")]
    assert all(e[2] - failed_at < 5 for e in errors)
    assert env.picker._previews == {} and env.picker._inflight == {}


def test_a_typed_elevenlabs_id_is_checked_before_a_preview(env, owner):
    session = env.picker.catalog._session
    session.routes[VOICE_URL] = FakeResponse(400, {"detail": {"status": "voice_not_found"}})
    rendered = []
    env.picker.engines._build = lambda voice: rendered.append(voice) or FakeEngine(voice)
    r = owner.post("/api/voices/preview", json={"voice": f"elevenlabs:{TYPED}"})
    assert r.status_code == 404 and r.json() == {"error": "unknown voice"}
    assert rendered == [] and env.picker._previews == {}  # nothing paid for or cached
    assert session.calls[-1][0] == VOICE_URL
    session.routes[VOICE_URL] = ConnectionError("down")
    r = owner.post("/api/voices/preview", json={"voice": f"elevenlabs:{TYPED}"})
    assert r.status_code == 503 and rendered == []
    session.routes[VOICE_URL] = FakeResponse(200, {"voice_id": TYPED, "name": "Clone"})
    r = owner.post("/api/voices/preview", json={"voice": f"elevenlabs:{TYPED}"})
    assert r.status_code == 200 and rendered == [f"elevenlabs:{TYPED}"]
