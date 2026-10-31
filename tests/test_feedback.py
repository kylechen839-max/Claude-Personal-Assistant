"""Beta feedback and the one-time survey (kwil/feedback.py): sealed per
person, capped, rate limited, never logged, owner-only to read, exported
and deleted with the person. All offline (test_userkeys' app)."""

import io
import json
import logging
import time
import zipfile

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("cryptography")

from kwil import feedback  # noqa: E402
from tests.test_userkeys import build_env  # noqa: E402
from tests.test_webapp import login  # noqa: E402

URL = "/api/me/feedback"
WORDS = "the calendar button SECRETWORD broke"


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = build_env(tmp_path, monkeypatch, turns_per_minute=0)
    e.config["connect"]["dir"] = str(tmp_path / "owner")
    yield e
    e.close()


@pytest.fixture
def owner(env):
    c = env.client()
    login(c)
    return c


def send(c, **body):
    return c.post(URL, json={"kind": "general", **body})


def test_a_note_is_sealed_in_their_folder_and_only_the_owner_reads_it(env, owner, caplog):
    tess, tess_id = env.member(owner, "Tess")
    with caplog.at_level(logging.DEBUG):
        r = send(tess, broke=WORDS, confused="", pay="$5 a month")
    assert r.status_code == 200 and r.json()["ok"] is True
    path = env.tmp / "users" / tess_id / "feedback.enc"
    raw = path.read_bytes()
    assert b"SECRETWORD" not in raw and b"month" not in raw
    # their words never reach the log at any level; INFO has id and lengths
    assert "SECRETWORD" not in caplog.text and "a month" not in caplog.text
    info = [r.getMessage() for r in caplog.records
            if r.name == "kwil.feedback" and r.levelno == logging.INFO]
    assert info == [f"Feedback: {tess_id} sent general feedback ({len(WORDS)}/0/10 chars)"]
    # owner only: 404 for the tester, 401 signed out
    assert tess.get("/api/feedback").status_code == 404
    assert env.client().get("/api/feedback").status_code == 401
    got = owner.get("/api/feedback").json()
    (person,) = got["people"]
    assert person["id"] == tess_id and person["name"] == "Tess"
    (entry,) = person["entries"]
    assert entry["broke"] == WORDS and entry["pay"] == "$5 a month" and entry["kind"] == "general"
    # the owner's own note goes in the owner's folder
    assert send(owner, confused="the history tab").status_code == 200
    assert (env.tmp / "owner" / "feedback.enc").exists()
    assert owner.get("/api/feedback").json()["owner"][0]["confused"] == "the history tab"


def test_caps_on_answers_body_and_entries(env, owner):
    tess, tess_id = env.member(owner, "Tess")
    assert send(tess, broke="x" * 2001).status_code == 400
    assert send(tess, broke="x" * 2000).status_code == 200
    big = json.dumps({"kind": "general", "broke": "y" * (feedback.MAX_BODY + 1)}).encode()
    assert tess.post(URL, content=big,
                     headers={"Content-Type": "application/json"}).status_code == 413
    assert send(tess).status_code == 400  # nothing written
    assert send(tess, kind="spam", broke="x").status_code == 400
    assert send(tess, broke=["not", "text"]).status_code == 400
    for i in range(feedback.MAX_ENTRIES - 1):
        feedback.add(env.fam, tess_id, {"broke": f"note {i}"})
    with pytest.raises(feedback.FeedbackError) as e:
        feedback.add(env.fam, tess_id, {"broke": "one too many"})
    assert e.value.status == 409
    assert len(feedback.entries(env.fam, tess_id)) == feedback.MAX_ENTRIES


def test_three_full_answers_in_chinese_fit(env, owner):
    """The page sends UTF-8 JSON (JSON.stringify keeps Chinese as is: 3
    bytes a character), so three 2,000-character answers are about 18 KB;
    under the old 8 KB cap they got a 413."""
    tess, tess_id = env.member(owner, "Tess")
    answer = "日历按钮坏了" * 333 + "了一"  # 2,000 characters
    assert len(answer) == feedback.MAX_ANSWER
    body = json.dumps({"kind": "survey", "broke": answer, "confused": answer, "pay": answer},
                      ensure_ascii=False).encode("utf-8")
    assert len(body) > 18_000
    r = tess.post(URL, content=body, headers={"Content-Type": "application/json"})
    assert r.status_code == 200, r.text
    (entry,) = feedback.entries(env.fam, tess_id)
    assert entry["pay"] == answer


def test_five_an_hour_per_person(env, owner):
    tess, _ = env.member(owner, "Tess")
    ugo, _ = env.member(owner, "Ugo")
    codes = [send(tess, broke=f"n{i}").status_code for i in range(6)]
    assert codes == [200] * 5 + [429]
    assert send(ugo, broke="mine").status_code == 200


def test_no_user_data_key_stores_nothing(env, owner, monkeypatch):
    tess, tess_id = env.member(owner, "Tess")
    monkeypatch.delenv("USER_DATA_KEY")
    assert send(tess, broke=WORDS).status_code == 503
    assert not (env.tmp / "users" / tess_id / "feedback.enc").exists()


def test_exported_with_their_data_and_deleted_with_them(env, owner):
    tess, tess_id = env.member(owner, "Tess")
    assert send(tess, broke=WORDS).status_code == 200
    r = tess.get("/api/me/export")
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        (entry,) = json.loads(z.read("feedback.json"))
    assert entry["broke"] == WORDS
    assert owner.post(f"/api/users/{tess_id}/remove", json={}).status_code == 200
    assert not (env.tmp / "users" / tess_id).exists()
    assert owner.get("/api/feedback").json()["people"] == []


def test_another_persons_sealed_file_doesnt_open_as_theirs(env, owner):
    tess, tess_id = env.member(owner, "Tess")
    ugo, ugo_id = env.member(owner, "Ugo")
    send(tess, broke=WORDS)
    src = env.tmp / "users" / tess_id / "feedback.enc"
    dst = env.tmp / "users" / ugo_id / "feedback.enc"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())
    assert feedback.entries(env.fam, ugo_id) == []


def test_the_survey_is_due_after_days_or_conversations_and_once(env, owner):
    tess, tess_id = env.member(owner, "Tess")
    user = env.fam.users[tess_id]
    assert isinstance(user["joined"], int)  # set at their first sign-in
    assert tess.get("/api/state").json()["feedback_due"] is False
    assert owner.get("/api/state").json()["feedback_due"] is False  # never the owner
    # three days after they joined
    env.fam.users[tess_id] = {**user, "joined": int(time.time()) - 3 * 86400}
    assert tess.get("/api/state").json()["feedback_due"] is True
    # answering it ends it, for good
    r = tess.post(URL, json={"kind": "survey", "broke": "", "confused": "x", "pay": "0"})
    assert r.status_code == 200
    assert env.fam.users[tess_id]["survey"] == "answered"
    assert tess.get("/api/state").json()["feedback_due"] is False
    # a family member never gets it
    mom, mom_id = env.member(owner, "Mom", tier="family")
    env.fam.users[mom_id] = {**env.fam.users[mom_id], "joined": 0}
    assert mom.get("/api/state").json()["feedback_due"] is False


def test_the_survey_is_due_after_enough_conversations_and_dismiss_ends_it(env, owner):
    env.fam.config["family"].update(feedback_after_days=0, feedback_after_conversations=1)
    ugo, ugo_id = env.member(owner, "Ugo", tier="beta")
    assert ugo.get("/api/state").json()["feedback_due"] is False
    store = env.fam.context(ugo_id).history
    conv = store.create("web")
    store.append(conv, "user", "hi")
    store.append(conv, "assistant", "Hello.")
    assert store.count(archived=None) == 1
    assert ugo.get("/api/state").json()["feedback_due"] is True
    r = ugo.post(URL + "/dismiss", content=b"", headers={"Content-Length": "0"})
    assert r.status_code == 200
    assert ugo.get("/api/state").json()["feedback_due"] is False
    # kept in users.json: still dismissed after a restart
    saved = json.loads((env.tmp / "users" / "users.json").read_text())
    assert saved["users"][ugo_id]["survey"] == "dismissed"
    # the owner has no survey to dismiss
    assert owner.post(URL + "/dismiss", content=b"",
                      headers={"Content-Length": "0"}).status_code == 404


def test_the_beta_links_reach_state_only_when_https(env, owner):
    tess, _ = env.member(owner, "Tess")
    f = env.fam.config["family"]
    f.update(notion_template_url="", beta_chat_url="")
    st = tess.get("/api/state").json()
    assert "notion_template_url" not in st and "beta_chat_url" not in st
    f.update(notion_template_url="http://notion.site/x", beta_chat_url="javascript:alert(1)")
    st = tess.get("/api/state").json()
    assert "notion_template_url" not in st and "beta_chat_url" not in st
    f.update(notion_template_url="https://x.notion.site/T", beta_chat_url="https://chat.example/j")
    st = tess.get("/api/state").json()
    assert st["notion_template_url"] == "https://x.notion.site/T"
    assert st["beta_chat_url"] == "https://chat.example/j"
