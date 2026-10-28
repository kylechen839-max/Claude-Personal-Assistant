"""Student piece P1: the deadline routes (kwil/student_web.py) for the
owner and invited people, the sealed Canvas link, sync, export and delete.

Offline: Canvas is an httpx.MockTransport (canvas.TRANSPORT) serving the
fixture feed, name resolution is faked, Claude is the conftest fake client.
The owner's CANVAS_* variables are cleared by conftest; tests set their own."""

import io
import json
import logging
import sys
import threading
import zipfile
from pathlib import Path

import httpx
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("cryptography")
from fastapi.testclient import TestClient  # noqa: E402

from kwil import canvas, cli, deadlines, family as family_mod, student_web  # noqa: E402
from kwil import userkeys, webapp  # noqa: E402
from kwil.chat import ClaudeChat  # noqa: E402
from kwil.config import ISOLATED_ENV_VARS, SECRET_ENV_VARS, load_config  # noqa: E402
from kwil.tools import ToolRegistry  # noqa: E402
from kwil.tts import PrintTTS  # noqa: E402
from tests.conftest import make_response, text_block  # noqa: E402
from tests.test_canvas import fake_resolve  # noqa: E402
from tests.test_webapp import BASE, TOKEN, Clock, FakeTranscriber, login  # noqa: E402

FEED = Path(__file__).parent / "fixtures" / "canvas_feed.ics"
SECRET = "s" * 48                                          # USER_DATA_KEY
FEED_SECRET = "Q" * 40
FEED_URL = f"https://canvas.example.edu/feeds/calendars/user_{FEED_SECRET}.ics"
CANVAS_TOKEN = "1234~" + "T" * 64
DEADLINE_TOOLS = {"list_deadlines", "confirm_deadline", "add_deadline", "delete_deadline"}


class FakeCanvasHost:
    def __init__(self):
        self.requests = []
        self.feed = FEED.read_bytes()

    def __call__(self, request):
        self.requests.append(request)
        if request.url.path.startswith("/feeds/calendars/"):
            return httpx.Response(200, content=self.feed,
                                  headers={"Content-Type": "text/calendar"})
        return httpx.Response(404)


@pytest.fixture
def s(fake_client, tmp_path, monkeypatch):
    monkeypatch.setenv("USER_DATA_KEY", SECRET)
    monkeypatch.setattr(canvas, "RESOLVE", fake_resolve)
    host = FakeCanvasHost()
    monkeypatch.setattr(canvas, "TRANSPORT", httpx.MockTransport(host))
    config = load_config(None, None)
    config["family"].update(enabled=True, dir=str(tmp_path / "users"))
    config["student"]["dir"] = str(tmp_path / "student")
    config["student"]["gmail"] = False      # the Gmail step has its own test
    config["web"]["enabled"] = False
    config["assistant"]["timezone"] = "America/Chicago"
    fam = family_mod.Family(config, client=fake_client, user_client=lambda key: fake_client)
    assistant = webapp.WebAssistant(
        lambda reg: ClaudeChat(client=fake_client, model="m", tools=reg),
        FakeTranscriber(), PrintTTS(), tools=ToolRegistry(), clock=Clock(), family=fam)
    app = webapp.create_app(assistant, TOKEN, reap_interval=3600,
                            sessions_file=tmp_path / "sessions.json")

    class S:
        pass

    e = S()
    e.__dict__.update(config=config, fam=fam, app=app, host=host, tmp=tmp_path,
                      client=fake_client, assistant=assistant, sync=app.state.student)

    def member(name="Tess", tier="beta"):
        _, invite = fam.invite(name, "en", "America/Chicago", tier=tier)
        c = TestClient(app, base_url=BASE)
        assert c.post("/api/invite/accept", json={"invite": invite}).status_code == 200
        uid = next(u["id"] for u in fam.members() if u["name"] == name)
        return c, uid

    def owner():
        c = TestClient(app, base_url=BASE)
        login(c)
        return c

    e.member, e.owner = member, owner
    yield e
    fam.close()


def connect(c, url=FEED_URL):
    r = c.post("/api/student/canvas", json={"ics_url": url})
    assert r.status_code == 200, r.text
    return r.json()


def delete(c, path):
    """DELETE as a browser sends it (the app wants a Content-Length)."""
    return c.request("DELETE", path, headers={"Content-Length": "0"})


def titles(c):
    return [i["title"] for i in c.get("/api/student/items").json()["items"]]


# --- connecting -----------------------------------------------------------------------

def test_member_connects_a_feed_sealed_at_rest_and_syncs(s):
    tess, uid = s.member()
    status = connect(tess)
    assert status["connected"] and status["kind"] == "ics" and status["managed"] == "app"
    assert status["school"] == "canvas.example.edu"
    assert FEED_SECRET not in json.dumps(status)
    path = s.tmp / "users" / uid / "canvas.enc"
    raw = path.read_bytes()
    assert FEED_SECRET.encode() not in raw and b"canvas.example.edu" not in raw
    if sys.platform != "win32":
        assert path.stat().st_mode & 0o077 == 0
    assert userkeys.unseal(canvas.CANVAS_LABEL, uid, raw, {"USER_DATA_KEY": SECRET}) == {
        "uid": uid, "kind": "ics", "ics_url": FEED_URL}
    # not openable as another kind of secret, nor as someone else's
    assert userkeys.unseal(userkeys.NOTION_LABEL, uid, raw, {"USER_DATA_KEY": SECRET}) is None
    assert userkeys.unseal(canvas.CANVAS_LABEL, "u_someoneelse1", raw,
                           {"USER_DATA_KEY": SECRET}) is None
    r = tess.post("/api/student/sync")
    assert r.status_code == 200, r.text
    assert r.json() == {"sources": {"ics": {"added": 3, "updated": 0, "unchanged": 0,
                                            "skipped": 0, "conflicts": 0}}, "errors": {}}
    assert [str(q.url) for q in s.host.requests] == [FEED_URL]
    body = tess.get("/api/student/items").json()
    assert [i["title"] for i in body["items"]] == ["Problem Set 5", "Reading Quiz: Chapter "
                                                   "7 and the long title that Canvas folds "
                                                   "across two lines", "Midterm Exam 2"]
    assert body["canvas"]["last_sync"]
    assert (s.tmp / "users" / uid / "deadlines.sqlite").exists()
    assert not (s.tmp / "student").exists()  # nothing in the owner's file


def test_fails_closed_without_user_data_key(s, monkeypatch):
    tess, uid = s.member()
    monkeypatch.delenv("USER_DATA_KEY")
    r = tess.post("/api/student/canvas", json={"ics_url": FEED_URL})
    assert r.status_code == 503 and "USER_DATA_KEY" in r.json()["error"]
    assert not (s.tmp / "users" / uid / "canvas.enc").exists()
    assert tess.get("/api/student/items").json()["canvas"]["can_store"] is False
    monkeypatch.setenv("USER_DATA_KEY", "short")
    assert tess.post("/api/student/canvas", json={"ics_url": FEED_URL}).status_code == 503


@pytest.mark.parametrize("body", [
    {"ics_url": "https://intranet.example.edu/feeds/calendars/user_x.ics"},
    {"ics_url": "http://canvas.example.edu/feeds/calendars/user_x.ics"},
    {"ics_url": "https://canvas.example.edu/courses/1"},
    {"base_url": "canvas.example.edu", "token": "my school password!"},
    {"base_url": "https://localhost", "token": CANVAS_TOKEN},
    {"password": "hunter2", "username": "tess"}, {}])
def test_bad_links_are_refused_and_nothing_is_written(s, body):
    tess, uid = s.member()
    r = tess.post("/api/student/canvas", json=body)
    assert r.status_code == 400
    assert "user_x" not in r.text and "hunter2" not in r.text and "password!" not in r.text
    assert not (s.tmp / "users" / uid / "canvas.enc").exists()


def test_api_token_link_syncs_the_planner(s, monkeypatch):
    from tests.test_canvas import TOKEN as FAKES_TOKEN, FakeCanvas

    assert FAKES_TOKEN == CANVAS_TOKEN
    fake = FakeCanvas()
    monkeypatch.setattr(canvas, "TRANSPORT", httpx.MockTransport(fake))
    tess, uid = s.member()
    r = tess.post("/api/student/canvas", json={"base_url": "canvas.example.edu/courses/9",
                                               "token": CANVAS_TOKEN})
    assert r.status_code == 200 and r.json()["kind"] == "api"
    assert CANVAS_TOKEN not in r.text
    raw = (s.tmp / "users" / uid / "canvas.enc").read_bytes()
    assert CANVAS_TOKEN.encode() not in raw
    out = tess.post("/api/student/sync").json()
    assert out["errors"] == {} and out["sources"]["canvas_api"]["added"] == 4
    assert {q.url.host for q in fake.requests} == {"canvas.example.edu"}


def test_owner_connects_in_the_app_sealed_at_rest_and_syncs(s):
    """Behaviour change (owner Canvas connect; it replaces
    test_owner_uses_the_environment_never_the_app): without CANVAS_ICS_URL /
    CANVAS_TOKEN the owner pastes the link in the app like an invited
    person; it is sealed to "owner" in <[student] dir>/canvas.enc."""
    kyle = s.owner()
    before = kyle.get("/api/student/items").json()["canvas"]
    assert before["connected"] is False and before["managed"] == "app"
    assert before["can_store"] is True
    status = connect(kyle)
    assert status["connected"] and status["kind"] == "ics" and status["managed"] == "app"
    assert status["school"] == "canvas.example.edu" and FEED_SECRET not in json.dumps(status)
    path = s.tmp / "student" / "canvas.enc"
    raw = path.read_bytes()
    assert FEED_SECRET.encode() not in raw and b"canvas.example.edu" not in raw
    if sys.platform != "win32":
        assert path.stat().st_mode & 0o077 == 0
    assert userkeys.unseal(canvas.CANVAS_LABEL, "owner", raw, {"USER_DATA_KEY": SECRET}) == {
        "uid": "owner", "kind": "ics", "ics_url": FEED_URL}
    assert not list((s.tmp / "users").rglob("canvas.enc"))
    r = kyle.post("/api/student/sync")
    assert r.status_code == 200, r.text
    assert r.json()["errors"] == {} and r.json()["sources"]["ics"]["added"] == 3
    assert [str(q.url) for q in s.host.requests] == [FEED_URL]
    assert len(titles(kyle)) == 3 and (s.tmp / "student" / "deadlines.sqlite").exists()
    # school + access token works for the owner too
    r = kyle.post("/api/student/canvas", json={"base_url": "canvas.example.edu",
                                               "token": CANVAS_TOKEN})
    assert r.status_code == 200 and r.json()["kind"] == "api"
    assert CANVAS_TOKEN not in r.text and CANVAS_TOKEN.encode() not in path.read_bytes()


def test_owner_link_is_checked_like_anyone_elses(s):
    kyle = s.owner()
    for body in ({"ics_url": "https://intranet.example.edu/feeds/calendars/user_x.ics"},
                 {"ics_url": "http://canvas.example.edu/feeds/calendars/user_x.ics"},
                 {"base_url": "https://localhost", "token": CANVAS_TOKEN}, {}):
        r = kyle.post("/api/student/canvas", json=body)
        assert r.status_code == 400 and "user_x" not in r.text
    assert not (s.tmp / "student" / "canvas.enc").exists()


def test_owner_environment_wins_while_it_is_set(s, monkeypatch):
    kyle = s.owner()
    monkeypatch.setenv("CANVAS_ICS_URL", FEED_URL)
    r = kyle.post("/api/student/canvas", json={"ics_url": FEED_URL})
    assert r.status_code == 409 and "CANVAS_ICS_URL" in r.json()["error"]
    status = kyle.get("/api/student/items").json()["canvas"]
    assert status["connected"] and status["managed"] == "env" and FEED_SECRET not in str(status)
    assert status["app_link"] is False
    # nothing of the app's to forget: the env stays, 409
    r = kyle.post("/api/student/disconnect", json={"delete_items": True})
    assert r.status_code == 409 and "CANVAS_ICS_URL" in r.json()["error"]
    assert kyle.post("/api/student/sync").json()["sources"]["ics"]["added"] == 3
    assert [str(q.url) for q in s.host.requests] == [FEED_URL]
    assert len(titles(kyle)) == 3
    # an invited person sees none of the owner's
    tess, uid = s.member()
    assert titles(tess) == []
    # a token in the env counts too, even without [canvas] base_url
    monkeypatch.delenv("CANVAS_ICS_URL")
    monkeypatch.setenv("CANVAS_TOKEN", CANVAS_TOKEN)
    assert kyle.post("/api/student/canvas", json={"ics_url": FEED_URL}).status_code == 409
    assert kyle.get("/api/student/items").json()["canvas"]["managed"] == "env"
    # once removed there, the app manages the link at once (no restart)
    monkeypatch.delenv("CANVAS_TOKEN")
    assert kyle.get("/api/student/items").json()["canvas"]["managed"] == "app"
    assert kyle.post("/api/student/disconnect", json={}).status_code == 200


def test_owner_can_forget_an_older_app_link_while_the_env_wins(s, monkeypatch):
    """Judge LOW 3: an app-stored link from before the env was set came
    back silently once the env vars were removed; Disconnect deletes it
    (never the env)."""
    kyle = s.owner()
    connect(kyle, FEED_URL.replace(FEED_SECRET, "A" * 40))
    path = s.tmp / "student" / "canvas.enc"
    monkeypatch.setenv("CANVAS_ICS_URL", FEED_URL)
    status = kyle.get("/api/student/items").json()["canvas"]
    assert status["managed"] == "env" and status["app_link"] is True
    assert kyle.post("/api/student/sync").json()["sources"]["ics"]["added"] == 3
    assert [str(q.url) for q in s.host.requests] == [FEED_URL]  # the env's, not the file's
    r = kyle.post("/api/student/disconnect", json={})
    assert r.status_code == 200 and r.json()["had"] is True
    assert not path.exists()
    status = kyle.get("/api/student/items").json()["canvas"]
    assert status["connected"] and status["managed"] == "env" and status["app_link"] is False
    assert len(titles(kyle)) == 3  # kept unless asked
    assert kyle.post("/api/student/disconnect", json={}).status_code == 409
    # with the env gone, the old link doesn't come back
    monkeypatch.delenv("CANVAS_ICS_URL")
    status = kyle.get("/api/student/items").json()["canvas"]
    assert status["connected"] is False and status["managed"] == "app"


def test_owner_disconnect_deletes_the_file_and_what_came_from_canvas(s):
    kyle = s.owner()
    connect(kyle)
    kyle.post("/api/student/sync")
    assert len(titles(kyle)) == 3
    r = kyle.post("/api/student/disconnect", json={"delete_items": True})
    assert r.status_code == 200, r.text
    assert r.json() == {"connected": False, "had": True, "deleted": 3}
    assert not (s.tmp / "student" / "canvas.enc").exists()
    assert titles(kyle) == []
    status = kyle.get("/api/student/items").json()["canvas"]
    assert status["connected"] is False and status["managed"] == "app"
    # kept items, then the per-source purge
    connect(kyle)  # a new link may sync at once
    assert kyle.post("/api/student/sync").json()["sources"]["ics"]["added"] == 3
    assert kyle.post("/api/student/disconnect", json={}).json()["deleted"] == 0
    assert len(titles(kyle)) == 3
    assert delete(kyle, "/api/student/source/ics").json() == {"ok": True, "deleted": 3}
    assert titles(kyle) == []


def test_owner_fails_closed_without_user_data_key(s, monkeypatch):
    kyle = s.owner()
    monkeypatch.delenv("USER_DATA_KEY")
    r = kyle.post("/api/student/canvas", json={"ics_url": FEED_URL})
    assert r.status_code == 503 and "USER_DATA_KEY" in r.json()["error"]
    assert not (s.tmp / "student" / "canvas.enc").exists()
    assert kyle.get("/api/student/items").json()["canvas"]["can_store"] is False
    # a stored link stays shut once the key is gone
    monkeypatch.setenv("USER_DATA_KEY", SECRET)
    connect(kyle)
    monkeypatch.delenv("USER_DATA_KEY")
    assert kyle.get("/api/student/items").json()["canvas"]["connected"] is False


def test_members_never_reach_the_owners_link(s):
    kyle = s.owner()
    connect(kyle)
    owner_file = s.tmp / "student" / "canvas.enc"
    sealed = owner_file.read_bytes()
    tess, uid = s.member()
    status = tess.get("/api/student/items").json()["canvas"]
    assert status["connected"] is False and status["managed"] == "app"
    assert tess.post("/api/student/sync").json()["sources"] == {}
    assert s.host.requests == []
    # her connect and disconnect touch only her own folder
    other = FEED_URL.replace(FEED_SECRET, "B" * 40)
    connect(tess, other)
    assert tess.post("/api/student/disconnect", json={"delete_items": True}).status_code == 200
    assert owner_file.read_bytes() == sealed
    # the owner's sealed file copied into her folder doesn't open as hers
    (s.tmp / "users" / uid / "canvas.enc").write_bytes(sealed)
    assert tess.get("/api/student/items").json()["canvas"]["connected"] is False
    assert s.sync.credentials(uid) is None
    # and hers never opens as the owner's
    (s.tmp / "users" / uid / "canvas.enc").unlink()
    connect(tess, other)
    owner_file.write_bytes((s.tmp / "users" / uid / "canvas.enc").read_bytes())
    assert s.sync.credentials(None) is None


def test_owner_connect_logs_no_link_or_token(s, caplog):
    caplog.set_level(logging.DEBUG)
    kyle = s.owner()
    connect(kyle)
    kyle.post("/api/student/sync")
    kyle.post("/api/student/canvas", json={"base_url": "canvas.example.edu",
                                           "token": CANVAS_TOKEN})
    kyle.post("/api/student/disconnect", json={"delete_items": True})
    ours = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("kwil"))
    for plain in (FEED_SECRET, FEED_URL, CANVAS_TOKEN, "canvas.example.edu", "Problem Set"):
        assert plain not in ours
    info = [r.getMessage() for r in caplog.records
            if r.name == "kwil.student_web" and r.levelno >= logging.INFO]
    assert "Student: the owner connected Canvas (ics)" in info
    assert "Student: the owner connected Canvas (api)" in info
    assert any(m.startswith("Student: the owner disconnected Canvas") for m in info)


def test_each_person_reaches_only_their_own_items(s, monkeypatch):
    monkeypatch.setenv("CANVAS_ICS_URL", FEED_URL)
    kyle = s.owner()
    kyle.post("/api/student/sync")
    kyle_ids = [i["id"] for i in kyle.get("/api/student/items").json()["items"]]
    tess, uid = s.member()
    for rid in kyle_ids:
        assert delete(tess, f"/api/student/items/{rid}").status_code == 404
        assert tess.post(f"/api/student/items/{rid}/resolve",
                         json={"choice": "dismiss"}).status_code == 404
    assert tess.post("/api/student/source/ics/delete").json() == {"ok": True, "deleted": 0}
    assert len(titles(kyle)) == 3
    anon = TestClient(s.app, base_url=BASE)
    assert anon.get("/api/student/items").status_code == 401
    assert anon.post("/api/student/sync").status_code == 401


# --- syncing ------------------------------------------------------------------------

def test_sync_is_throttled_per_person_and_one_at_a_time(s):
    tess, uid = s.member()
    connect(tess)
    assert tess.post("/api/student/sync").status_code == 200
    r = tess.post("/api/student/sync")
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 800
    # someone else isn't held up
    sam, _ = s.member("Sam")
    assert sam.post("/api/student/sync").status_code == 200
    # one running: a second is refused at once
    s.sync._last.clear()
    s.sync._running[uid] = threading.Lock()
    s.sync._running[uid].acquire()
    try:
        r = tess.post("/api/student/sync")
        assert r.status_code == 409
    finally:
        s.sync._running[uid].release()
    # a new link may sync at once
    s.sync._last[uid] = s.sync.clock()
    connect(tess)
    assert tess.post("/api/student/sync").status_code == 200


def test_resync_is_idempotent_and_confirmed_dates_hold_through_the_routes(s):
    tess, uid = s.member()
    connect(tess)
    tess.post("/api/student/sync")
    first = tess.get("/api/student/items").json()["items"]
    pset = first[0]
    r = tess.post(f"/api/student/items/{pset['id']}/resolve", json={"choice": "confirm"})
    assert r.status_code == 200 and r.json()["item"]["status"] == "confirmed"
    s.host.feed = FEED.read_bytes().replace(b"DTSTART:20261104T045900Z",
                                            b"DTSTART:20261111T045900Z")
    s.sync._last.clear()
    out = tess.post("/api/student/sync").json()
    assert out["sources"]["ics"]["added"] == 0
    after = tess.get("/api/student/items").json()["items"]
    assert len(after) == 3
    row = next(i for i in after if i["id"] == pset["id"])
    assert row["due"] == pset["due"] and row["source_due"] == "2026-11-11T04:59:00Z"
    assert tess.post(f"/api/student/items/{pset['id']}/resolve",
                     json={"choice": "someday"}).status_code == 400


def test_a_failed_feed_is_reported_by_code_only(s, caplog):
    caplog.set_level(logging.DEBUG)
    tess, uid = s.member()
    connect(tess)
    s.host.feed = b"<html>Your session has expired " + FEED_SECRET.encode() + b"</html>"
    out = tess.post("/api/student/sync").json()
    assert out == {"sources": {}, "errors": {"ics": "not_a_calendar"}}
    ours = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("kwil"))
    assert FEED_SECRET not in ours and "session has expired" not in ours


def test_a_whole_sync_has_one_deadline(s, monkeypatch):
    """Judge round 2, LOW 4: past [the sync's] time nothing more is fetched."""
    s.config["student"]["gmail"] = True
    tess, uid = s.member(tier="family")
    connect(tess)
    searched = []
    inbox = type("Inbox", (), {"search": lambda self, q, n: searched.append(q) or []})()
    monkeypatch.setattr(s.fam, "gmail_inbox", lambda user, info=None: inbox)
    monkeypatch.setattr(canvas, "SYNC_SECONDS", -1)
    out = tess.post("/api/student/sync").json()
    assert out == {"sources": {}, "errors": {"ics": "timed_out", "gmail": "timed_out"}}
    assert s.host.requests == [] and searched == []


def test_a_password_is_never_stored(s):
    """Judge round 2, LOW 6: a password field is ignored, never stored (and
    SECURITY.md says so, not that it is refused)."""
    tess, uid = s.member()
    r = tess.post("/api/student/canvas", json={"ics_url": FEED_URL, "password": "hunter2"})
    assert r.status_code == 200
    raw = (s.tmp / "users" / uid / "canvas.enc").read_bytes()
    assert userkeys.unseal(canvas.CANVAS_LABEL, uid, raw, {"USER_DATA_KEY": SECRET}) == {
        "uid": uid, "kind": "ics", "ics_url": FEED_URL}
    security = (Path(__file__).parent.parent / "SECURITY.md").read_text(encoding="utf-8")
    assert "a body with a password is a 400" not in security
    assert "a password field is ignored and never stored" in security


def test_delete_one_and_a_whole_source(s):
    tess, uid = s.member()
    connect(tess)
    tess.post("/api/student/sync")
    items = tess.get("/api/student/items").json()["items"]
    assert delete(tess, f"/api/student/items/{items[0]['id']}").json() == {"ok": True,
                                                                         "deleted": 1}
    assert tess.post(f"/api/student/items/{items[1]['id']}/delete").status_code == 200
    assert delete(tess, f"/api/student/items/{items[1]['id']}").status_code == 404
    assert delete(tess, "/api/student/items/abc").status_code == 404
    assert len(titles(tess)) == 1
    s.sync._last.clear()
    tess.post("/api/student/sync")
    assert len(titles(tess)) == 1  # deleted ones don't come back
    assert delete(tess, "/api/student/source/ics").json() == {"ok": True, "deleted": 1}
    assert delete(tess, "/api/student/source/myspace").status_code == 404
    assert titles(tess) == []


def test_disconnect_forgets_the_link_and_optionally_the_items(s):
    tess, uid = s.member()
    connect(tess)
    tess.post("/api/student/sync")
    r = tess.post("/api/student/disconnect", json={})
    assert r.json() == {"connected": False, "had": True, "deleted": 0}
    assert not (s.tmp / "users" / uid / "canvas.enc").exists()
    assert len(titles(tess)) == 3
    connect(tess)
    r = tess.post("/api/student/disconnect", json={"delete_items": True})
    assert r.json()["deleted"] == 3 and titles(tess) == []


def test_gmail_notifications_are_read_once_through_claude(s, monkeypatch):
    s.config["student"]["gmail"] = True
    sam, uid = s.member("Sam", tier="family")  # the owner's key: no BYOK needed
    inbox = type("Inbox", (), {})()
    searches = []

    def search(q, n):
        searches.append((q, n))
        return [{"id": "m1", "subject": "Assignment Graded: Lab 3",
                 "snippet": "Lab 4 is due Nov 2 at 11:59pm", "date": "2026-10-20T08:00-05:00"}]

    inbox.search = search
    monkeypatch.setattr(s.fam, "gmail_inbox", lambda user, info=None: inbox)
    s.client.queue.append(make_response(text_block(json.dumps({"items": [
        {"text_index": 0, "title": "Lab 4", "course": "CHEM 102", "kind": "assignment",
         "due": "2026-11-02T23:59"}]}))))
    out = sam.post("/api/student/sync").json()
    assert out["sources"]["gmail"]["added"] == 1 and out["errors"] == {}
    assert searches == [(canvas.GMAIL_QUERY, 20)]
    call = s.client.calls[-1]
    assert call["output_config"]["format"]["schema"] == canvas.EXTRACT_SCHEMA
    assert call["model"] == s.config["assistant"]["fast_model"]
    item = sam.get("/api/student/items").json()["items"][0]
    assert item["source"] == "gmail" and item["due"] == "2026-11-03T05:59:00Z"  # CST by then
    assert item["status"] == "draft"
    s.sync._last.clear()
    calls = len(s.client.calls)
    sam.post("/api/student/sync")
    assert len(s.client.calls) == calls  # that email was read already


def test_a_beta_tester_without_a_key_skips_the_email_step(s, monkeypatch):
    s.config["student"]["gmail"] = True
    tess, uid = s.member(tier="beta")
    inbox = type("Inbox", (), {"search": lambda self, q, n: [
        {"id": "m1", "subject": "x", "snippet": "y", "date": ""}]})()
    monkeypatch.setattr(s.fam, "gmail_inbox", lambda user, info=None: inbox)
    calls = len(s.client.calls)
    out = tess.post("/api/student/sync").json()
    assert out["errors"] == {"gmail": "no_key"} and len(s.client.calls) == calls


# --- the person's own tools, export and delete ---------------------------------------

def test_member_tools_use_their_own_file(s):
    tess, uid = s.member()
    connect(tess)
    tess.post("/api/student/sync")
    tools = s.fam.context(uid).registry.tools
    assert DEADLINE_TOOLS <= set(tools)
    assert tools["list_deadlines"].handler(days=60)["count"] == 3
    # the owner's tools never see it
    owner_tools = cli.build_tools(s.config, lambda *_: None)
    assert owner_tools.tools["list_deadlines"].handler(days=60)["count"] == 0


def test_export_has_deadlines_never_the_link_and_delete_removes_everything(s):
    tess, uid = s.member()
    connect(tess)
    tess.post("/api/student/sync")
    r = tess.get("/api/me/export")
    assert r.status_code == 200
    files = {n: zipfile.ZipFile(io.BytesIO(r.content)).read(n).decode()
             for n in zipfile.ZipFile(io.BytesIO(r.content)).namelist()}
    rows = json.loads(files["deadlines.json"])
    assert {row["title"] for row in rows} >= {"Problem Set 5", "Midterm Exam 2"}
    blob = "\n".join(files.values())
    assert FEED_SECRET not in blob and "canvas.enc" not in blob
    folder = s.tmp / "users" / uid
    assert (folder / "deadlines.sqlite").exists() and (folder / "canvas.enc").exists()
    r = tess.post("/api/me/delete", json={"confirm": "DELETE"})
    assert r.status_code == 200, r.text
    assert not folder.exists()
    # a sync for someone just removed writes nothing back
    with pytest.raises(KeyError):
        s.fam.deadlines.propose(uid, [{"source_key": "k", "title": "x",
                                       "due": "2026-11-02"}], "ics")
    assert not folder.exists()


def test_removed_mid_sync_never_brings_the_folder_back(s, monkeypatch):
    tess, uid = s.member()
    connect(tess)
    real = canvas.fetch_ics

    def fetch_then_removed(url, http=None, **kw):
        data = real(url, http, **kw)
        s.fam.remove(uid)
        return data

    monkeypatch.setattr(canvas, "fetch_ics", fetch_then_removed)
    r = tess.post("/api/student/sync")
    assert r.status_code == 404
    assert not (s.tmp / "users" / uid).exists()


def test_student_off_is_404(fake_client, tmp_path, monkeypatch):
    monkeypatch.setenv("USER_DATA_KEY", SECRET)
    config = load_config(None, None)
    config["family"].update(enabled=True, dir=str(tmp_path / "users"))
    config["student"].update(enabled=False, dir=str(tmp_path / "student"))
    fam = family_mod.Family(config, client=fake_client)
    assert fam.deadlines is None
    assistant = webapp.WebAssistant(
        lambda reg: ClaudeChat(client=fake_client, model="m", tools=reg),
        FakeTranscriber(), PrintTTS(), tools=ToolRegistry(), clock=Clock(), family=fam)
    app = webapp.create_app(assistant, TOKEN, reap_interval=3600,
                            sessions_file=tmp_path / "sessions.json")
    c = TestClient(app, base_url=BASE)
    login(c)
    for method, path in (("get", "/api/student/items"), ("post", "/api/student/sync"),
                         ("post", "/api/student/canvas")):
        assert getattr(c, method)(path).status_code == 404
    fam.close()


def test_owner_without_family_accounts_uses_the_process_store(fake_client, tmp_path,
                                                              monkeypatch):
    monkeypatch.setattr(canvas, "RESOLVE", fake_resolve)
    host = FakeCanvasHost()
    monkeypatch.setattr(canvas, "TRANSPORT", httpx.MockTransport(host))
    monkeypatch.setenv("CANVAS_ICS_URL", FEED_URL)
    config = load_config(None, None)
    config["student"]["dir"] = str(tmp_path / "student")
    config["family"]["dir"] = str(tmp_path / "users")
    config["student"]["gmail"] = False
    tools = cli.build_tools(config, lambda *_: None)  # opens the store, as build_assistant
    assert deadlines.current().owner_dir == tmp_path / "student"
    assistant = webapp.WebAssistant(
        lambda reg: ClaudeChat(client=fake_client, model="m", tools=reg),
        FakeTranscriber(), PrintTTS(), tools=tools, clock=Clock())
    app = webapp.create_app(assistant, TOKEN, reap_interval=3600,
                            sessions_file=tmp_path / "sessions.json")
    c = TestClient(app, base_url=BASE)
    login(c)
    assert c.post("/api/student/sync").json()["sources"]["ics"]["added"] == 3
    assert tools.tools["list_deadlines"].handler(days=60)["count"] == 3


def _pc_owner_app(fake_client, tmp_path, monkeypatch):
    """The PC's own app: no family accounts (family=None), the process store."""
    monkeypatch.setattr(canvas, "RESOLVE", fake_resolve)
    host = FakeCanvasHost()
    monkeypatch.setattr(canvas, "TRANSPORT", httpx.MockTransport(host))
    config = load_config(None, None)
    config["student"]["dir"] = str(tmp_path / "student")
    config["family"]["dir"] = str(tmp_path / "users")
    config["student"]["gmail"] = False
    tools = cli.build_tools(config, lambda *_: None)
    assistant = webapp.WebAssistant(
        lambda reg: ClaudeChat(client=fake_client, model="m", tools=reg),
        FakeTranscriber(), PrintTTS(), tools=tools, clock=Clock())
    app = webapp.create_app(assistant, TOKEN, reap_interval=3600,
                            sessions_file=tmp_path / "sessions.json")
    assert app.state.student.family is None
    c = TestClient(app, base_url=BASE)
    login(c)
    return c, host


def test_owner_without_family_accounts_connects_in_the_app(fake_client, tmp_path,
                                                           monkeypatch):
    """Judge LOW 4: the PC owner path (family=None) seals, syncs, deletes."""
    monkeypatch.setenv("USER_DATA_KEY", SECRET)
    c, host = _pc_owner_app(fake_client, tmp_path, monkeypatch)
    status = connect(c)
    assert status["managed"] == "app" and status["kind"] == "ics"
    path = tmp_path / "student" / "canvas.enc"
    raw = path.read_bytes()
    assert FEED_SECRET.encode() not in raw
    assert userkeys.unseal(canvas.CANVAS_LABEL, "owner", raw, {"USER_DATA_KEY": SECRET}) == {
        "uid": "owner", "kind": "ics", "ics_url": FEED_URL}
    assert c.post("/api/student/sync").json()["sources"]["ics"]["added"] == 3
    assert [str(q.url) for q in host.requests] == [FEED_URL]
    r = c.post("/api/student/disconnect", json={"delete_items": True})
    assert r.status_code == 200 and r.json() == {"connected": False, "had": True, "deleted": 3}
    assert not path.exists()


def test_owner_without_family_accounts_fails_closed_without_key(fake_client, tmp_path,
                                                                monkeypatch):
    monkeypatch.delenv("USER_DATA_KEY", raising=False)
    c, _ = _pc_owner_app(fake_client, tmp_path, monkeypatch)
    r = c.post("/api/student/canvas", json={"ics_url": FEED_URL})
    assert r.status_code == 503 and "USER_DATA_KEY" in r.json()["error"]
    assert not (tmp_path / "student" / "canvas.enc").exists()
    assert c.get("/api/student/items").json()["canvas"]["can_store"] is False


def test_school_lookup_is_rate_limited(s, monkeypatch):
    monkeypatch.setattr(canvas, "find_school", lambda q, http=None: [
        {"name": "Example U", "base_url": "https://canvas.example.edu"}])
    tess, _ = s.member()
    for _ in range(student_web.SCHOOL_LOOKUPS):
        assert tess.get("/api/student/schools", params={"q": "Example"}).status_code == 200
    assert tess.get("/api/student/schools", params={"q": "Example"}).status_code == 429


def test_logs_hold_no_link_token_or_title(s, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    tess, uid = s.member()
    connect(tess)
    tess.post("/api/student/sync")
    rid = tess.get("/api/student/items").json()["items"][0]["id"]
    tess.post(f"/api/student/items/{rid}/resolve", json={"choice": "confirm"})
    delete(tess, f"/api/student/items/{rid}")
    tess.post("/api/student/canvas", json={"base_url": "canvas.example.edu",
                                           "token": CANVAS_TOKEN})
    tess.post("/api/student/disconnect", json={"delete_items": True})
    ours = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("kwil"))
    for plain in (FEED_SECRET, FEED_URL, CANVAS_TOKEN, "Problem Set", "MATH 241",
                  "Midterm", "canvas.example.edu"):
        assert plain not in ours
    assert uid in ours  # ids are fine


def test_the_card_is_on_the_page(s):
    """Judge round 2, LOW 7: student.js was written but never loaded."""
    from kwil import cloud

    web = Path(__file__).parent.parent / "kwil" / "web"
    page = (web / "index.html").read_text(encoding="utf-8")
    assert '<script src="/student.js" defer></script>' in page
    assert page.index("/i18n.js") < page.index("/student.js")
    assert '"/student.js"' in (web / "sw.js").read_text(encoding="utf-8")
    assert "/student.js" in cloud.PUBLIC_GET
    r = TestClient(s.app, base_url=BASE).get("/student.js")
    assert r.status_code == 200 and "KwilStudent" in r.text
    assert r.headers["content-type"].startswith("text/javascript")


def test_canvas_secrets_are_listed_as_secret_and_isolated():
    for var in ("CANVAS_TOKEN", "CANVAS_ICS_URL"):
        assert var in SECRET_ENV_VARS and var in ISOLATED_ENV_VARS


# --- the way in, picked for the school (canvas_schools.py) ----------------------------

def _seed(s, schools):
    """A fixture seed (the shipped one has no schools yet)."""
    path = s.tmp / "seed.json"
    path.write_text(json.dumps({"version": 1, "schools": schools}), encoding="utf-8")
    s.sync.policies.seed_path, s.sync.policies._seed = path, None


BLOCKS = {"name": "Example U", "hosts": ["canvas.example.edu"], "domains": [],
          "student_tokens": "blocked", "since": None, "source": "https://it.example.edu/t",
          "checked": "2026-10-01", "note": ""}


def test_schools_report_token_policy(s, monkeypatch):
    """Each school the lookup finds says whether it lets students make a
    token (the seed, else what was learned, else unknown); a typed
    address asks /api/student/canvas/policy."""
    _seed(s, [BLOCKS])
    s.sync.policies.record("canvas.other.edu", "allowed")
    monkeypatch.setattr(canvas, "find_school", lambda q, http=None: [
        {"name": "Example U", "base_url": "https://canvas.example.edu"},
        {"name": "Other U", "base_url": "https://canvas.other.edu"},
        {"name": "New U", "base_url": "https://canvas.new.edu"}])
    tess, _ = s.member()
    r = tess.get("/api/student/schools", params={"q": "Example"})
    assert r.status_code == 200
    assert [(x["name"], x["tokens"]) for x in r.json()["schools"]] == [
        ("Example U", "blocked"), ("Other U", "allowed"), ("New U", "unknown")]
    r = tess.get("/api/student/canvas/policy", params={"host": "https://canvas.example.edu/x"})
    assert r.json() == {"host": "canvas.example.edu", "tokens": "blocked"}
    assert r.headers["cache-control"] == "no-store"
    for bad in ("localhost", "10.0.0.1", "http://canvas.example.edu", ""):
        assert tess.get("/api/student/canvas/policy", params={"host": bad}).status_code == 400
    # the lookups share one budget
    for _ in range(student_web.SCHOOL_LOOKUPS - 2):
        tess.get("/api/student/canvas/policy", params={"host": "canvas.new.edu"})
    assert tess.get("/api/student/canvas/policy",
                    params={"host": "canvas.new.edu"}).status_code == 429


def test_a_working_token_sync_records_allowed_and_a_401_nothing(s, monkeypatch):
    from tests.test_canvas import FakeCanvas

    _seed(s, [])
    monkeypatch.setattr(canvas, "TRANSPORT", httpx.MockTransport(FakeCanvas()))
    tess, _ = s.member()
    # a token Canvas refuses (expired, revoked): says nothing about the policy
    r = tess.post("/api/student/canvas", json={"base_url": "canvas.example.edu",
                                               "token": "1234~" + "X" * 64})
    assert r.status_code == 200
    out = tess.post("/api/student/sync").json()
    assert "canvas_api" in out["errors"]
    assert not s.sync.policies.path.exists()
    assert s.sync.policies.policy("canvas.example.edu") == "unknown"
    # a token that works: the school lets students make them
    s.sync._last.clear()
    tess.post("/api/student/canvas", json={"base_url": "canvas.example.edu",
                                           "token": CANVAS_TOKEN})
    out = tess.post("/api/student/sync").json()
    assert out["errors"] == {}
    assert s.sync.policies.policy("canvas.example.edu") == "allowed"
    assert tess.get("/api/student/items").json()["canvas"]["policy"] == {
        "host": "canvas.example.edu", "tokens": "allowed"}
    learned = json.loads(s.sync.policies.path.read_text(encoding="utf-8"))
    assert list(learned) == ["canvas.example.edu"]
    assert _uid_free(learned, s)


def _uid_free(data, s) -> bool:
    blob = json.dumps(data)
    return all(u["id"] not in blob and u["name"] not in blob for u in s.fam.members())


def test_reporting_no_token_button_records_blocked(s):
    _seed(s, [])
    tess, _ = s.member()
    body = {"host": "canvas.new.edu", "tokens": "blocked"}
    r = tess.post("/api/student/canvas/report", json=body)
    assert r.status_code == 200 and r.json() == {"host": "canvas.new.edu", "tokens": "blocked"}
    assert s.sync.policies.policy("canvas.new.edu") == "blocked"
    # only "blocked", only a Canvas host
    for bad in ({"host": "canvas.new.edu", "tokens": "allowed"},
                {"host": "localhost", "tokens": "blocked"}, {"tokens": "blocked"}, {}):
        assert tess.post("/api/student/canvas/report", json=bad).status_code == 400
    # a POST needs its Content-Length (create_app's 411)
    r = tess.request("POST", "/api/student/canvas/report",
                     headers={"Transfer-Encoding": "chunked"}, content=iter([b"{}"]))
    assert r.status_code == 411
    assert _uid_free(json.loads(s.sync.policies.path.read_text(encoding="utf-8")), s)


def test_a_report_never_overrides_a_seed_checked_later(s):
    """A person's report is a claim: dated today, it only beats a seed
    checked before today."""
    _seed(s, [{**BLOCKS, "student_tokens": "allowed", "checked": "2999-01-01"}])
    tess, _ = s.member()
    r = tess.post("/api/student/canvas/report",
                  json={"host": "canvas.example.edu", "tokens": "blocked"})
    assert r.status_code == 200 and r.json()["tokens"] == "allowed"


def test_reports_are_throttled_per_person(s):
    _seed(s, [])
    tess, _ = s.member()
    body = {"host": "canvas.new.edu", "tokens": "blocked"}
    for _ in range(student_web.REPORTS):
        assert tess.post("/api/student/canvas/report", json=body).status_code == 200
    assert tess.post("/api/student/canvas/report", json=body).status_code == 429
    learned = json.loads(s.sync.policies.path.read_text(encoding="utf-8"))
    assert learned["canvas.new.edu"]["blocked"] == student_web.REPORTS
    # someone else has their own budget
    sam, _ = s.member("Sam")
    assert sam.post("/api/student/canvas/report", json=body).status_code == 200


def test_policy_routes_are_404_when_student_is_off(fake_client, tmp_path, monkeypatch):
    monkeypatch.setenv("USER_DATA_KEY", SECRET)
    config = load_config(None, None)
    config["family"].update(enabled=True, dir=str(tmp_path / "users"))
    config["student"].update(enabled=False, dir=str(tmp_path / "student"))
    fam = family_mod.Family(config, client=fake_client)
    assistant = webapp.WebAssistant(
        lambda reg: ClaudeChat(client=fake_client, model="m", tools=reg),
        FakeTranscriber(), PrintTTS(), tools=ToolRegistry(), clock=Clock(), family=fam)
    app = webapp.create_app(assistant, TOKEN, reap_interval=3600,
                            sessions_file=tmp_path / "sessions.json")
    c = TestClient(app, base_url=BASE)
    login(c)
    assert c.get("/api/student/canvas/policy", params={"host": "canvas.x.edu"}).status_code == 404
    assert c.post("/api/student/canvas/report",
                  json={"host": "canvas.x.edu", "tokens": "blocked"}).status_code == 404
    assert not (tmp_path / "student").exists()
    fam.close()


def test_tokens_for_owner_keeps_tokens_from_invited_people(s, monkeypatch):
    """[canvas] tokens_for = "owner": a member's token is refused with a
    clear code (their feed still works), the owner's is taken, and the
    status tells the page; "everyone" (the default) is today's way."""
    from tests.test_canvas import FakeCanvas

    monkeypatch.setattr(canvas, "TRANSPORT", httpx.MockTransport(FakeCanvas()))
    assert s.config["canvas"]["tokens_for"] == "everyone"
    tess, uid = s.member()
    assert tess.get("/api/student/items").json()["canvas"]["tokens_offered"] is True
    s.config["canvas"]["tokens_for"] = "owner"
    assert tess.get("/api/student/items").json()["canvas"]["tokens_offered"] is False
    r = tess.post("/api/student/canvas", json={"base_url": "canvas.example.edu",
                                               "token": CANVAS_TOKEN})
    assert r.status_code == 403 and r.json()["code"] == "tokens_owner_only"
    assert CANVAS_TOKEN not in r.text
    assert not (s.tmp / "users" / uid / "canvas.enc").exists()
    owner = s.owner()
    assert owner.get("/api/student/items").json()["canvas"]["tokens_offered"] is True
    r = owner.post("/api/student/canvas", json={"base_url": "canvas.example.edu",
                                                "token": CANVAS_TOKEN})
    assert r.status_code == 200 and r.json()["kind"] == "api"
    # anything but "everyone" is the stricter "owner"
    s.config["canvas"]["tokens_for"] = "owners"
    assert tess.post("/api/student/canvas", json={"base_url": "canvas.example.edu",
                                                  "token": CANVAS_TOKEN}).status_code == 403
    connect(tess)   # the feed still works for them


def test_policy_logs_hold_no_host(s, monkeypatch, caplog):
    from tests.test_canvas import FakeCanvas

    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(canvas, "TRANSPORT", httpx.MockTransport(FakeCanvas()))
    _seed(s, [])
    tess, uid = s.member()
    tess.post("/api/student/canvas/report", json={"host": "canvas.secret.edu",
                                                  "tokens": "blocked"})
    tess.get("/api/student/canvas/policy", params={"host": "canvas.secret.edu"})
    tess.post("/api/student/canvas", json={"base_url": "canvas.example.edu",
                                           "token": CANVAS_TOKEN})
    tess.post("/api/student/sync")
    assert s.sync.policies.policy("canvas.example.edu") == "allowed"
    ours = [r for r in caplog.records if r.name.startswith("kwil")]
    assert any("reported" in r.getMessage() for r in ours)
    for r in ours:
        assert "canvas.secret.edu" not in r.getMessage()
        assert "canvas.example.edu" not in r.getMessage()
