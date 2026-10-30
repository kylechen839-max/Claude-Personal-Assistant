"""Per-person limits for invited people (kwil/quotas.py): turns per
minute and per day, speech seconds per day, never the owner; enforced on
push-to-talk / typed turns (WebAssistant.start_turn) and the hands-free
voice view (VoiceConversation._turn_body), with a friendly sentence in the
person's language and no call to Claude. All offline (the mocked client)."""

import json
import logging
from datetime import datetime, timezone

import numpy as np
import pytest

from kwil import i18n
from kwil.config import DEFAULTS
from kwil.quotas import UserQuotas

pytest.importorskip("fastapi")
from tests.conftest import make_response, text_block  # noqa: E402
from tests.test_family import env, owner  # noqa: E402,F401 (fixtures)
from tests.test_voice_ws import connect, receive, utterance  # noqa: E402
from tests.test_webapp import FakeTranscriber, wav_bytes  # noqa: E402

DAY = 86400
# 2023-11-14 22:13:20 UTC
T0 = 1_700_000_000.0


class Now:
    def __init__(self, now=T0):
        self.now = now

    def __call__(self):
        return self.now


def quotas(clock, **kw):
    kw = {"turns_per_minute": 0, "turns_per_day": 0, "stt_seconds_per_day": 0, **kw}
    return UserQuotas(clock=clock, **kw)


# --- the counters ---------------------------------------------------------------

def test_turns_per_minute_refuses_at_the_limit_and_the_next_minute_is_fine():
    clock = Now()
    q = quotas(clock, turns_per_minute=3)
    assert [q.admit("u_a") for _ in range(3)] == [None, None, None]
    assert q.admit("u_a") == "limit_minute"
    assert q.check("u_a") == "limit_minute"
    assert q.admit("u_b") is None  # per person
    clock.now += 59.9
    assert q.admit("u_a") == "limit_minute"  # a refusal isn't counted...
    clock.now += 0.2  # ...so the window is the first turn's
    assert q.admit("u_a") is None


def test_turns_per_day_refuses_at_the_limit_and_resets_the_next_day():
    clock = Now(datetime(2026, 10, 21, 12, tzinfo=timezone.utc).timestamp())
    q = quotas(clock, turns_per_day=2)
    assert q.admit("u_a") is None and q.admit("u_a") is None
    assert q.admit("u_a") == "limit_day"
    clock.now += 11 * 3600 + 59 * 60  # 23:59 UTC: still today
    assert q.admit("u_a") == "limit_day"
    clock.now += 60  # midnight UTC (no time zone: UTC)
    assert q.admit("u_a") is None


def test_the_day_is_the_persons_own():
    """Asia/Shanghai is UTC+8: their day starts at 16:00 UTC."""
    clock = Now(datetime(2026, 10, 21, 15, 59, tzinfo=timezone.utc).timestamp())
    zones = {"u_sh": "Asia/Shanghai", "u_utc": "", "u_bad": "Not/AZone"}
    q = quotas(clock, turns_per_day=1, timezone_of=zones.get)
    for uid in zones:
        assert q.admit(uid) is None and q.admit(uid) == "limit_day"
    clock.now += 60  # 16:00 UTC = midnight in Shanghai
    assert q.admit("u_sh") is None
    assert q.admit("u_utc") == "limit_day"
    assert q.admit("u_bad") == "limit_day"  # an unknown zone counts as UTC


def test_speech_seconds_refuse_recordings_but_not_typing():
    clock = Now()
    q = quotas(clock, stt_seconds_per_day=10)
    q.add_speech("u_a", 9.9)
    assert q.check("u_a", audio=True) is None
    q.add_speech("u_a", 0.2)  # a clip started under the limit still counts
    assert q.check("u_a", audio=True) == "limit_speech"
    assert q.admit("u_a", audio=True) == "limit_speech"
    assert q.admit("u_a") is None  # typing still works
    assert q.check("u_b", audio=True) is None
    clock.now += DAY
    assert q.admit("u_a", audio=True) is None


def test_zero_means_no_limit():
    q = quotas(Now())
    q.add_speech("u_a", 10 ** 6)
    assert all(q.admit("u_a", audio=True) is None for _ in range(500))


def test_memory_stays_bounded():
    clock = Now()
    q = quotas(clock, turns_per_minute=5, max_tracked=4)
    for i in range(50):
        q.admit(f"u_{i}")
        assert len(q.usage) <= 4
    # people with nothing left to count (the minute and their day are over)
    # are pruned as soon as room is needed
    clock.now += 2 * DAY
    q.admit("u_new")
    assert list(q.usage) == ["u_new"]
    q.forget("u_new")
    assert q.usage == {}


def test_beta_defaults_and_config():
    f = DEFAULTS["family"]
    assert (f["turns_per_minute"], f["turns_per_day"], f["stt_seconds_per_day"]) == \
        (6, 200, 1800)
    q = UserQuotas.from_config(DEFAULTS)
    assert (q.turns_per_minute, q.turns_per_day, q.stt_seconds_per_day) == (6, 200, 1800)


def test_refusals_are_said_in_both_languages():
    values = {"per_minute": 6, "per_day": 200, "minutes": 30}
    for key in ("limit_minute", "limit_day", "limit_speech"):
        en, zh = i18n.t(key, "en", **values), i18n.t(key, "zh", **values)
        assert en and zh and en != zh
    assert "200" in i18n.t("limit_day", "zh", **values)
    assert "30" in i18n.t("limit_speech", "en", **values)


# --- the web app ------------------------------------------------------------------

def events(r) -> list[dict]:
    return [json.loads(line) for line in r.text.splitlines() if line.strip()]


def member(env, owner, language="en"):
    """A signed-in family member (the owner's key: no key of their own)."""
    client, link = env.member(owner, name="Ann", language=language, tier="family")
    return client, link["user"]["id"]


def session_of(env, uid):
    return next(s for s in env.assistant.sessions.values() if s.user == uid)


def test_web_turns_are_limited_with_nothing_sent_or_saved(env, owner):
    env.family.quotas.turns_per_minute = 2
    ann, uid = member(env, owner)
    for word in ("one", "two"):
        env.fake_client.queue.append(make_response(text_block(f"Reply {word}.")))
        assert events(ann.post("/api/turn", json={"text": word}))[-2]["type"] == "reply"
    s = session_of(env, uid)
    s.chat.recorder.flush()
    calls, history = len(env.fake_client.calls), list(s.chat.history)
    saved = env.family.context(uid).history.get(s.chat.conversation_id)["messages"]

    r = ann.post("/api/turn", json={"text": "three"})
    assert r.status_code == 200
    # "done" still says which conversation the page is in (the same one)
    assert events(r) == [{"type": "error", "message": i18n.t("limit_minute", "en")},
                         {"type": "done", "conversation": s.chat.conversation_id,
                          "redo": True}]
    assert s.chat.conversation_id
    assert len(env.fake_client.calls) == calls  # Claude was never asked
    assert s.chat.history == history  # the conversation as it was
    s.chat.recorder.flush()
    assert env.family.context(uid).history.get(
        s.chat.conversation_id)["messages"] == saved
    assert not s.busy.locked()  # and the next turn can start

    env.clock.now += 61  # the family's clock: the next minute
    env.fake_client.queue.append(make_response(text_block("Reply three.")))
    assert events(ann.post("/api/turn", json={"text": "three"}))[-2]["text"] == \
        "Reply three."


def test_the_refusal_is_in_their_language(env, owner):
    env.family.quotas.turns_per_day = 1
    mei, _ = member(env, owner, language="zh")
    env.fake_client.queue.append(make_response(text_block("你好。")))
    mei.post("/api/turn", json={"text": "你好"})
    out = events(mei.post("/api/turn", json={"text": "再来"}))
    assert out[0] == {"type": "error",
                      "message": "你今天的 1 条消息已经用完了，明天再来找我吧。"}


def test_the_owner_is_never_limited(env, owner):
    q = env.family.quotas
    q.turns_per_minute, q.turns_per_day, q.stt_seconds_per_day = 1, 1, 1
    env.assistant.transcriber = FakeTranscriber("one", "two", "three")
    for i in range(3):
        env.fake_client.queue.append(make_response(text_block(f"Reply {i}.")))
        r = owner.post("/api/turn", files={"audio": ("a.wav", wav_bytes(2), "audio/wav")})
        reply = events(r)[-2]
        assert (reply["type"], reply["text"]) == ("reply", f"Reply {i}.")
    assert q.usage == {}  # nothing even counted


def test_web_recordings_count_speech_and_are_refused_before_whisper(env, owner):
    q = env.family.quotas
    q.stt_seconds_per_day = 1.5
    ann, uid = member(env, owner)
    env.assistant.transcriber = FakeTranscriber("hello")
    env.fake_client.queue.append(make_response(text_block("Hi.")))
    r = ann.post("/api/turn", files={"audio": ("a.wav", wav_bytes(2), "audio/wav")})
    assert events(r)[0] == {"type": "transcript", "text": "hello"}
    assert q.usage[uid].speech == pytest.approx(2.0)  # the audio's length
    calls = len(env.fake_client.calls)
    r = ann.post("/api/turn", files={"audio": ("a.wav", wav_bytes(1), "audio/wav")})
    assert events(r)[0] == {"type": "error",
                            "message": i18n.t("limit_speech", "en", minutes=1)}
    assert len(env.assistant.transcriber.heard) == 1  # Whisper never ran
    assert len(env.fake_client.calls) == calls
    env.fake_client.queue.append(make_response(text_block("Typed is fine.")))
    assert events(ann.post("/api/turn", json={"text": "hi"}))[-2]["text"] == \
        "Typed is fine."


def test_every_transcription_for_a_person_counts(env, owner):
    """A spoken yes/no (/api/confirm) or any other recording: counted,
    also when Whisper fails; the owner's never."""
    ann, uid = member(env, owner)
    a = env.assistant

    class Broken:
        def transcribe(self, recording):
            raise RuntimeError("whisper broke")

    a.transcriber = Broken()
    with pytest.raises(RuntimeError):
        a.transcribe(np.zeros(16000 * 3, dtype=np.float32), "en", user=uid)
    a.transcriber = FakeTranscriber("yes", "yes")
    a.transcribe_upload(wav_bytes(1), language="en", user=uid)
    a.transcribe(np.zeros(16000, dtype=np.float32), "en")  # the owner's
    assert env.family.quotas.usage[uid].speech == pytest.approx(4.0)
    assert list(env.family.quotas.usage) == [uid]


def test_a_spoken_answer_counts_toward_their_speech(env, owner):
    from kwil.webapp import PendingConfirm

    ann, uid = member(env, owner)
    env.fake_client.queue.append(make_response(text_block("Hi.")))
    ann.post("/api/turn", json={"text": "hi"})
    s = session_of(env, uid)
    s.pending = PendingConfirm("Delete it?")
    env.assistant.transcriber = FakeTranscriber("yes")
    r = ann.post("/api/confirm", files={"audio": ("a.wav", wav_bytes(1.5), "audio/wav")})
    assert r.status_code == 200 and s.pending.answer is True
    assert env.family.quotas.usage[uid].speech == pytest.approx(1.5)


def asked(env, uid, ann):
    """A question pending in their push-to-talk session (as _confirm sets)."""
    from kwil.webapp import PendingConfirm

    env.fake_client.queue.append(make_response(text_block("Hi.")))
    ann.post("/api/turn", json={"text": "hi"})
    s = session_of(env, uid)
    s.pending = PendingConfirm("Delete it?")
    return s, s.pending


def post_all(ann, n):
    """`n` spoken answers sent at once; their responses."""
    import threading

    out = [None] * n

    def send(i):
        out[i] = ann.post("/api/confirm",
                          files={"audio": ("a.wav", wav_bytes(1), "audio/wav")})

    threads = [threading.Thread(target=send, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    return out


def test_a_spoken_answer_with_no_speech_left_isnt_heard_and_stays_open(
        env, owner, caplog):
    """Out of speech time: Whisper never runs (not even for a burst of
    recordings) and it is never a "no"; the buttons still answer it."""
    q = env.family.quotas
    q.stt_seconds_per_day = 60
    ann, uid = member(env, owner)
    s, pending = asked(env, uid, ann)
    q.add_speech(uid, 60)
    env.assistant.transcriber = FakeTranscriber(*["yes"] * 5)
    with caplog.at_level(logging.DEBUG):
        responses = post_all(ann, 5)
    info = [r.getMessage() for r in caplog.records if r.levelno >= logging.INFO]
    assert f"Limits: refused a spoken answer for {uid} (limit_speech)" in info
    everything = "\n".join(r.getMessage() for r in caplog.records)
    assert "Ann" not in everything and "Delete it?" not in everything
    for r in responses:
        assert r.status_code == 429
        assert r.json() == {"error": i18n.t("limit_speech", "en", minutes=1)}
    assert env.assistant.transcriber.heard == []  # Whisper never ran
    assert s.pending is pending and not pending.answered.is_set()
    assert not pending.answering
    assert q.usage[uid].speech == pytest.approx(60)  # nothing more counted
    r = ann.post("/api/confirm", json={"answer": "yes"})  # a tap still works
    assert r.status_code == 200 and pending.answer is True


def test_only_one_spoken_answer_is_transcribed_at_a_time(env, owner):
    import threading

    ann, uid = member(env, owner)
    s, pending = asked(env, uid, ann)
    entered, go = threading.Event(), threading.Event()

    class Slow:
        heard = 0

        def transcribe(self, recording):
            Slow.heard += 1
            entered.set()
            go.wait(10)
            return "yes"

    env.assistant.transcriber = Slow()
    first = []
    t = threading.Thread(target=lambda: first.append(ann.post(
        "/api/confirm", files={"audio": ("a.wav", wav_bytes(1), "audio/wav")})))
    t.start()
    assert entered.wait(10)
    others = post_all(ann, 4)
    assert [r.status_code for r in others] == [409] * 4
    assert all(r.json() == {"error": "already answering"} for r in others)
    go.set()
    t.join(10)
    assert first[0].status_code == 200 and pending.answer is True
    assert Slow.heard == 1  # Whisper ran exactly once
    assert env.family.quotas.usage[uid].speech == pytest.approx(1.0)



def test_a_tap_while_a_recording_is_heard_leaves_no_stray_transcript(env, owner):
    """They tap Yes while their recording is still being transcribed: the
    tap answers, and the late "no" neither answers nor shows in the turn."""
    import queue
    import threading

    ann, uid = member(env, owner)
    s, pending = asked(env, uid, ann)
    entered, go = threading.Event(), threading.Event()

    class Slow:
        def transcribe(self, recording):
            entered.set()
            go.wait(10)
            return "no"

    env.assistant.transcriber = Slow()
    late = []
    t = threading.Thread(target=lambda: late.append(ann.post(
        "/api/confirm", files={"audio": ("a.wav", wav_bytes(1), "audio/wav")})))
    t.start()
    assert entered.wait(10)
    assert env.assistant.answer(s, True, confirm_id=pending.id)  # the tap
    go.set()
    t.join(10)
    assert late[0].status_code == 409 and pending.answer is True
    shown = []
    while True:
        try:
            shown.append(s.events.get_nowait())
        except queue.Empty:
            break
    assert not [e for e in shown if e.get("type") == "transcript"]

def test_a_failed_spoken_answer_can_be_tried_again(env, owner):
    ann, uid = member(env, owner)
    s, pending = asked(env, uid, ann)

    class Broken:
        def transcribe(self, recording):
            raise RuntimeError("whisper broke")

    env.assistant.transcriber = Broken()
    try:
        r = ann.post("/api/confirm", files={"audio": ("a.wav", wav_bytes(1), "audio/wav")})
        assert r.status_code >= 500
    except RuntimeError:
        pass  # the test client re-raises the server's error
    assert not pending.answering and not pending.answered.is_set()
    r = ann.post("/api/confirm", files={"audio": ("a.wav", b"not audio", "audio/wav")})
    assert 400 <= r.status_code < 500  # unreadable: also released
    assert not pending.answering
    env.assistant.transcriber = FakeTranscriber("yes")
    r = ann.post("/api/confirm", files={"audio": ("a.wav", wav_bytes(1), "audio/wav")})
    assert r.status_code == 200 and pending.answer is True


def test_a_spoken_answer_isnt_a_new_turn(env, owner):
    """At the turns-per-minute limit, the turn already running can still
    get its spoken yes (only speech time is checked)."""
    env.family.quotas.turns_per_minute = 1
    ann, uid = member(env, owner)
    s, pending = asked(env, uid, ann)
    env.assistant.transcriber = FakeTranscriber("yes")
    r = ann.post("/api/confirm", files={"audio": ("a.wav", wav_bytes(1), "audio/wav")})
    assert r.status_code == 200 and pending.answer is True


def test_a_turn_in_flight_cant_bring_back_a_removed_persons_counts(env, owner):
    ann, uid = member(env, owner)
    q = env.family.quotas
    assert q.admit(uid) is None  # a turn admitted, still running...
    env.family.remove(uid)       # ...when they are removed
    q.add_speech(uid, 2.0)       # its recording finishes afterwards
    assert q.admit(uid) is None and q.check(uid, audio=True) is None
    assert uid not in q.usage


def test_unlisted_ids_are_never_counted():
    listed = {"u_a"}
    q = quotas(Now(), turns_per_day=1, is_listed=listed.__contains__)
    assert q.admit("u_a") is None
    listed.clear()
    q.forget("u_a")
    q.add_speech("u_a", 5)
    assert q.admit("u_a") is None
    assert q.usage == {}


def test_removing_someone_forgets_their_counts(env, owner):
    ann, uid = member(env, owner)
    env.fake_client.queue.append(make_response(text_block("Hi.")))
    ann.post("/api/turn", json={"text": "hi"})
    assert uid in env.family.quotas.usage
    env.family.remove(uid)
    assert uid not in env.family.quotas.usage


def test_refusals_log_ids_and_reasons_only(env, owner, caplog):
    env.family.quotas.turns_per_day = 1
    ann, uid = member(env, owner)
    env.fake_client.queue.append(make_response(text_block("Hi.")))
    ann.post("/api/turn", json={"text": "first private words"})
    with caplog.at_level(logging.DEBUG):
        ann.post("/api/turn", json={"text": "second private words"})
    info = [r.getMessage() for r in caplog.records if r.levelno >= logging.INFO]
    assert f"Limits: refused a turn for {uid} (limit_day)" in info
    everything = "\n".join(r.getMessage() for r in caplog.records)
    assert "private words" not in everything and "Ann" not in everything
    assert i18n.t("limit_day", "en", per_day=1) not in everything


# --- the voice view ---------------------------------------------------------------

def voice_turn(ws, turn, seconds=1.0):
    ws.send_bytes(utterance(turn, seconds))
    return receive(ws, "turn_done")


def test_voice_turns_are_limited(env, owner):
    env.family.quotas.turns_per_minute = 1
    ann, uid = member(env, owner)
    env.assistant.transcriber = FakeTranscriber("first question", "")
    env.fake_client.queue.append(make_response(text_block("First answer.")))
    with connect(ann) as ws:
        assert json.loads(ws.receive_text())["type"] == "ready"
        first = voice_turn(ws, 1)
        assert [m["text"] for m in first if m["type"] == "audio"] == ["First answer."]
        calls = len(env.fake_client.calls)
        second = voice_turn(ws, 2)
        message = i18n.t("limit_minute", "en")
        assert {"type": "error", "turn": 2, "message": message} in second
        assert [m["text"] for m in second if m["type"] == "audio"] == [message]
        assert not [m for m in second if m["type"] == "transcript"]  # no Whisper
        assert len(env.fake_client.calls) == calls  # Claude never asked
        env.clock.now += 61
        third = voice_turn(ws, 3)  # heard as nothing: not counted, not refused
        assert [m["text"] for m in third if m["type"] == "transcript"] == [""]
        assert not [m for m in third if m["type"] == "error"]
        ws.send_text(json.dumps({"type": "end"}))
        receive(ws, "ended")
    assert len(env.fake_client.calls) == calls
    assert env.assistant.transcriber.texts == []
    u = env.family.quotas.usage[uid]
    assert (len(u.minute), u.turns) == (0, 1)  # only turn 1, a minute ago


def test_voice_speech_limit_is_checked_before_whisper(env, owner):
    q = env.family.quotas
    q.stt_seconds_per_day = 1.5
    mei, uid = member(env, owner, language="zh")
    env.assistant.transcriber = FakeTranscriber("你好")
    env.fake_client.queue.append(make_response(text_block("你好！")))
    with connect(mei) as ws:
        ws.receive_text()
        voice_turn(ws, 1, seconds=2)
        assert q.usage[uid].speech == pytest.approx(2.0)
        calls = len(env.fake_client.calls)
        second = voice_turn(ws, 2)
        message = i18n.t("limit_speech", "zh", minutes=1)
        assert {"type": "error", "turn": 2, "message": message} in second
        assert not [m for m in second if m["type"] == "transcript"]
        ws.send_text(json.dumps({"type": "end"}))
        receive(ws, "ended")
    assert env.assistant.transcriber.texts == []  # only the first was heard
    assert len(env.assistant.transcriber.heard) == 1
    assert len(env.fake_client.calls) == calls
    assert q.usage[uid].speech == pytest.approx(2.0)  # the refused one isn't counted


def test_the_owners_voice_is_never_limited(env, owner):
    q = env.family.quotas
    q.turns_per_minute, q.stt_seconds_per_day = 1, 1
    env.assistant.transcriber = FakeTranscriber("one", "two")
    for word in ("One.", "Two."):
        env.fake_client.queue.append(make_response(text_block(word)))
    with connect(owner) as ws:
        ws.receive_text()
        for turn, word in ((1, "One."), (2, "Two.")):
            msgs = voice_turn(ws, turn, seconds=2)
            assert [m["text"] for m in msgs if m["type"] == "audio"] == [word]
        ws.send_text(json.dumps({"type": "end"}))
        receive(ws, "ended")
    assert q.usage == {}
