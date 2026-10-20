"""The PC hub (assistant/devhub.py): loopback-only routes behind the web
app's sign-in, sending through the Claude Code CLI (mocked) and the reply,
pickup and presence rows from the Notion inbox (mocked)."""

import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from assistant import devhub, webapp  # noqa: E402
from assistant.config import DEFAULTS  # noqa: E402
from assistant.devteam import CoordinatorLink  # noqa: E402
from tests.devteam_fakes import SID, FakeNotion, FakeRun, ok_run, row  # noqa: E402
from tests.test_webapp import BASE, login, make_app  # noqa: E402,F401

SID2 = "cse_0123456789abcdefABCDEF"
LOOPBACK = ("127.0.0.1", 50000)
SECRET = "the secret plan for the voice speed setting"


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def config(tmp_path, **team):
    cfg = json.loads(json.dumps(DEFAULTS))
    team = {"hub_sessions": [SID, SID2], **team}
    cfg["devteam"].update(hub=True, hub_state_file=str(tmp_path / "devhub.json"),
                          **team)
    return cfg


def make_hub(tmp_path, run=None, notion=None, **team):
    cfg = config(tmp_path, **team)
    link = CoordinatorLink(SID, run=run or ok_run(), which=lambda c: f"/usr/bin/{c}",
                           state_file=cfg["devteam"]["hub_state_file"],
                           notion_client=notion or FakeNotion())
    return devhub.Hub(cfg, link, clock=Clock())


@pytest.fixture
def hub_app(make_app, tmp_path):
    def make(run=None, notion=None, **team):
        hub = make_hub(tmp_path, run, notion, **team)
        _, client = make_app()
        devhub.install(client.app, hub)
        local = TestClient(client.app, base_url=BASE, client=LOOPBACK)
        return local, hub
    return make


ROUTES = [("get", "/api/devteam/state"), ("post", "/api/devteam/send"),
          ("post", "/api/devteam/clear")]
PAGES = ["/devteam", "/devteam.js", "/devteam.css"]


@pytest.mark.parametrize("method,path", ROUTES)
def test_api_needs_sign_in(hub_app, method, path):
    client, _ = hub_app()
    kwargs = {} if method == "get" else {"json": {"session": SID, "text": "x"}}
    assert getattr(client, method)(path, **kwargs).status_code == 401


@pytest.mark.parametrize("headers", [{"x-forwarded-for": "100.64.0.9"},
                                     {"tailscale-user-login": "kyle@example.com"}])
def test_proxied_requests_get_404_even_signed_in(hub_app, headers):
    client, _ = hub_app()
    login(client)
    for path in PAGES + ["/api/devteam/state"]:
        assert client.get(path, headers=headers).status_code == 404, path
    r = client.post("/api/devteam/send", json={"session": SID, "text": "x"}, headers=headers)
    assert r.status_code == 404


def test_non_loopback_peer_gets_404(hub_app):
    client, _ = hub_app()
    remote = TestClient(client.app, base_url=BASE)  # peer "testclient"
    login(remote)
    assert remote.get("/devteam").status_code == 404
    assert remote.get("/api/devteam/state").status_code == 404


def test_trusted_proxy_peer_gets_404(make_app, tmp_path):
    hub = make_hub(tmp_path)
    _, client = make_app(trusted_proxy="127.0.0.1")
    devhub.install(client.app, hub)
    local = TestClient(client.app, base_url=BASE, client=LOOPBACK)
    assert local.get("/devteam").status_code == 404


def test_cross_origin_post_is_refused(hub_app):
    client, _ = hub_app()
    login(client)
    r = client.post("/api/devteam/send", json={"session": SID, "text": "x"},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_page_is_served_locally_with_the_strict_csp(hub_app):
    client, _ = hub_app()
    r = client.get("/devteam")
    assert r.status_code == 200 and "script-src 'self'" in r.headers["content-security-policy"]
    assert '<script src="/devteam.js"></script>' in r.text and "<script>" not in r.text
    assert client.get("/devteam.js").headers["content-type"].startswith("text/javascript")


def test_send_passes_stdin_hub_id_and_cloud(hub_app):
    run = ok_run()
    client, hub = hub_app(run=run)
    login(client)
    r = client.post("/api/devteam/send", json={"session": SID2, "text": SECRET})
    assert r.status_code == 200, r.text
    assert r.json()["message"]["stage"] == "queued"
    args, kw = run.calls[0]
    assert args == ["/usr/bin/claude", "-p", "--cloud", SID2, "--output-format", "json"]
    assert SECRET not in " ".join(args)
    assert kw["input"].startswith("[Hey Claude hub | reply id hub-")
    assert SECRET in kw["input"]
    assert hub.link.pending()[0].startswith("hub-")
    w = client.get("/api/devteam/state").json()["windows"]
    assert [x["session"] for x in w] == [SID, SID2]
    assert w[1]["queued"] is True and w[1]["messages"][0]["text"] == SECRET


def test_cli_failure_shows_not_sent(hub_app):
    run = FakeRun(stdout=json.dumps({"ok": False, "session_id": SID,
                                     "error": "cloud session is archived"}), returncode=1)
    client, hub = hub_app(run=run)
    login(client)
    r = client.post("/api/devteam/send", json={"session": SID, "text": "hi"})
    assert r.status_code == 502 and "archived" in r.json()["error"]
    msg = client.get("/api/devteam/state").json()["windows"][0]["messages"][0]
    assert msg["stage"] == "not sent" and "archived" in msg["error"]
    assert hub.link.pending() == []


def test_unknown_window_and_empty_text(hub_app):
    client, _ = hub_app()
    login(client)
    assert client.post("/api/devteam/send", json={"session": "session_nope", "text": "x"}).status_code == 404
    assert client.post("/api/devteam/send", json={"session": SID, "text": "  "}).status_code == 400
    assert client.post("/api/devteam/clear", json={"session": ["x"]}).status_code == 404


def test_one_send_in_flight_per_window(tmp_path):
    hub = make_hub(tmp_path)
    hub._send_locks[SID].acquire()
    with pytest.raises(BlockingIOError):
        hub.send(SID, "x")
    assert hub.send(SID2, "other window")["stage"] == "queued"


def test_stages_queued_working_answered(hub_app):
    notion = FakeNotion()
    client, hub = hub_app(notion=notion)
    login(client)
    client.post("/api/devteam/send", json={"session": SID, "text": "build it"})
    rid = hub.link.pending()[0]

    def msgs():
        hub.clock.t += 10  # past poll_seconds
        return client.get("/api/devteam/state").json()["windows"][0]["messages"]

    assert msgs()[0]["stage"] == "queued"
    notion.rows = [row(rid, "Reading your message", status="working")]
    m = msgs()
    assert m[0]["stage"] == "working" and m[0]["step"] == "Reading your message"
    notion.rows = [row(rid, "## Done\nThe PR is up.")]
    m = msgs()
    assert m[0]["stage"] == "answered" and "step" not in m[0]
    assert m[1]["from"] == "coordinator" and m[1]["text"] == "## Done\nThe PR is up."
    assert hub.link.pending() == []
    assert len(msgs()) == 2  # the same row is not added twice


def test_blocked_reply(tmp_path):
    notion = FakeNotion()
    hub = make_hub(tmp_path, notion=notion)
    rid = hub.send(SID, "x")["reply_id"]
    notion.rows = [row(rid, "Need your Render key.", status="blocked")]
    hub.poll()
    msgs = hub.state()["windows"][0]["messages"]
    assert msgs[0]["stage"] == "blocked" and msgs[1]["status"] == "blocked"


def test_poll_is_throttled(tmp_path):
    notion = FakeNotion()
    hub = make_hub(tmp_path, notion=notion, poll_seconds=4)
    hub.send(SID, "x")
    hub.poll()
    hub.poll()
    hub.clock.t += 3
    hub.poll()
    assert len(notion.queries) == 2  # one replies + one presence query
    hub.clock.t += 2
    hub.poll()
    assert len(notion.queries) == 4


def test_hub_and_voice_links_never_take_each_others_replies(tmp_path):
    notion = FakeNotion()
    hub = make_hub(tmp_path, notion=notion)
    voice = CoordinatorLink(SID, run=ok_run(), which=lambda c: "/usr/bin/claude",
                            state_file=tmp_path / "devteam.json", notion_client=notion)
    h = hub.send(SID, "hub message")["reply_id"]
    v = voice.send("voice message")["reply_id"]
    notion.rows = [row(h, "hub answer"), row(v, "voice answer")]
    assert [r["reply"] for r in voice.replies()] == ["voice answer"]
    hub.poll()
    texts = [m["text"] for m in hub.state()["windows"][0]["messages"]]
    assert texts == ["hub message", "hub answer"]
    assert json.loads((tmp_path / "devteam.json").read_text())["pending"] == {}


@pytest.mark.parametrize("status, minutes, state", [
    ("working", 2, "task"), ("working", 5, "task"), ("working", 6, "maybe"),
    ("working", 30, "maybe"), ("working", 31, "idle"), ("idle", 1, "idle"),
    ("", 1, "unknown")])
def test_presence_mapping(status, minutes, state):
    now = datetime(2026, 10, 20, 15, 30, tzinfo=timezone.utc)
    at = (now - timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")
    got = devhub.presence_state({"status": status, "text": "Judging", "at": at},
                                now.timestamp())
    assert got["state"] == state
    assert got["text"] == ("Judging" if state == "task" else "")


def test_presence_reaches_the_window(tmp_path):
    notion = FakeNotion()
    hub = make_hub(tmp_path, notion=notion)
    tail = SID.split("_", 1)[1]
    edited = datetime.fromtimestamp(hub.clock.t - 60, timezone.utc).isoformat()
    notion.rows = [row(f"presence-{tail}", "Running tests", status="working", edited=edited)]
    hub.poll()
    w = hub.state()["windows"]
    assert w[0]["presence"] == {"state": "task", "text": "Running tests", "age": 60}
    assert w[1]["presence"]["state"] == "unknown"


def test_clear_removes_the_text_from_the_file(hub_app, tmp_path):
    notion = FakeNotion()
    client, hub = hub_app(notion=notion)
    login(client)
    client.post("/api/devteam/send", json={"session": SID, "text": SECRET})
    client.post("/api/devteam/send", json={"session": SID2, "text": "keep me"})
    rid = next(m["reply_id"] for m in hub.link._state["windows"][SID])
    assert SECRET in (tmp_path / "devhub.json").read_text()
    assert client.post("/api/devteam/clear", json={"session": SID}).status_code == 200
    raw = (tmp_path / "devhub.json").read_text()
    assert SECRET not in raw and rid not in raw and "keep me" in raw
    notion.rows = [row(rid, "late answer to a cleared message")]
    hub.clock.t += 10
    w = client.get("/api/devteam/state").json()["windows"]
    assert w[0]["messages"] == []


def test_bounds(tmp_path):
    run = ok_run()
    hub = make_hub(tmp_path, run=run)
    hub.send(SID, "z" * 20000)
    assert len(hub.state()["windows"][0]["messages"][0]["text"]) == 12000
    assert "z" * 12001 not in run.calls[0][1]["input"]
    for i in range(205):
        hub.send(SID2, f"m{i}")
    msgs = hub.state()["windows"][1]["messages"]
    assert len(msgs) == 200 and msgs[0]["text"] == "m5"
    many = [f"session_{'a' * i}" for i in range(1, 12)]
    assert len(devhub.hub_sessions({"hub_sessions": many})) == 8
    # A single string is one id, never iterated character by character.
    assert devhub.hub_sessions({"hub_sessions": SID}) == [SID]
    assert devhub.hub_sessions({"hub_sessions": [], "coordinator_session": SID}) == [SID]
    assert devhub.hub_sessions({"hub_sessions": ["nope", SID, SID, 7]}) == [SID]


def test_no_message_or_reply_text_at_info(tmp_path, caplog):
    notion = FakeNotion()
    hub = make_hub(tmp_path, notion=notion)
    caplog.set_level(logging.INFO)
    rid = hub.send(SID, SECRET)["reply_id"]
    notion.rows = [row(rid, "the secret reply text")]
    hub.poll()
    hub.clear(SID)
    failing = make_hub(tmp_path / "f", run=FakeRun(stderr="Error: not signed in\n", returncode=1))
    (tmp_path / "f").mkdir(exist_ok=True)
    failing.send(SID, SECRET)
    infos = " ".join(r.getMessage() for r in caplog.records if r.levelno >= logging.INFO)
    assert "secret" not in infos
    assert rid in infos  # ids and lengths are fine


def test_off_by_default_and_off_off_loopback():
    cfg = json.loads(json.dumps(DEFAULTS))
    assert cfg["devteam"]["hub"] is False
    assert devhub.enabled(cfg, "127.0.0.1") is False
    cfg["devteam"]["hub"] = True
    assert devhub.enabled(cfg, "127.0.0.1") and devhub.enabled(cfg, "localhost")
    assert not devhub.enabled(cfg, "0.0.0.0")
    assert not devhub.enabled(cfg, "100.64.0.2")


def test_routes_absent_without_a_hub(make_app):
    _, client = make_app()
    local = TestClient(client.app, base_url=BASE, client=LOOPBACK)
    login(local)
    assert local.get("/devteam").status_code == 404
    assert local.get("/api/devteam/state").status_code == 404


def test_serve_builds_the_hub_only_on_loopback(monkeypatch, tmp_path):
    import uvicorn

    built = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: None)
    monkeypatch.setattr(webapp, "build_assistant", lambda *a, **k: object())
    monkeypatch.setattr(webapp, "create_app", lambda *a, **kw: built.append(kw.get("hub")))
    monkeypatch.setenv(webapp.TOKEN_ENV, "x" * 40)
    monkeypatch.setattr("assistant.webfeatures.Features.resume_jobs", lambda self: None)
    cfg = config(tmp_path)
    webapp.serve(cfg, host="127.0.0.1", port=8765, env_file=tmp_path / ".env",
                 output_fn=lambda m: None)
    webapp.serve(cfg, host="0.0.0.0", port=8765, env_file=tmp_path / ".env",
                 output_fn=lambda m: None)
    assert isinstance(built[0], devhub.Hub) and built[1] is None
    assert built[0].sessions == [SID, SID2]


def test_removed_window_is_deleted_on_disk(tmp_path):
    """A session dropped from hub_sessions: its transcript leaves the file
    and its reply ids stop being pending, so a late answer never lands."""
    notion = FakeNotion()
    hub = make_hub(tmp_path, notion=notion)
    rid = hub.send(SID2, SECRET)["reply_id"]
    hub.link._state.setdefault("seen", {})[rid] = ["working", "on it"]
    hub.link._save()
    hub.send(SID, "keep me")
    again = make_hub(tmp_path, notion=notion, hub_sessions=[SID])
    raw = (tmp_path / "devhub.json").read_text()
    assert SECRET not in raw and rid not in raw and "keep me" in raw
    assert rid not in again.link.pending()


def test_hub_window_frees_after_a_windows_cli_timeout(tmp_path, monkeypatch):
    """npm claude.cmd that hangs: after the timeout the window answers
    "not sent" and takes the next message (no 409 until the CLI quits)."""
    import subprocess

    from assistant import devteam
    from tests.devteam_fakes import TreePopen

    TreePopen.made, TreePopen.tree_killed = [], set()
    monkeypatch.setattr(subprocess, "Popen", TreePopen)
    monkeypatch.setattr(devteam, "_WINDOWS", True)
    cfg = config(tmp_path)
    link = CoordinatorLink(SID, which=lambda c: f"/usr/bin/{c}", timeout=0.01,
                           state_file=cfg["devteam"]["hub_state_file"],
                           notion_client=FakeNotion())
    hub = devhub.Hub(cfg, link, clock=Clock())
    assert hub.send(SID, "first")["stage"] == "not sent"
    assert not hub._send_locks[SID].locked()
    assert hub.send(SID, "second")["stage"] == "not sent"  # not BlockingIOError
    assert any(p.args[:2] == ["taskkill", "/T"] for p in TreePopen.made)


def test_serve_warns_that_a_trusted_proxy_turns_the_hub_off(monkeypatch, tmp_path, caplog):
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: None)
    monkeypatch.setattr(webapp, "build_assistant", lambda *a, **k: object())
    monkeypatch.setattr(webapp, "create_app", lambda *a, **kw: None)
    monkeypatch.setenv(webapp.TOKEN_ENV, "x" * 40)
    monkeypatch.setattr("assistant.webfeatures.Features.resume_jobs", lambda self: None)
    cfg = config(tmp_path)
    out = []
    webapp.serve(cfg, host="127.0.0.1", port=8765, env_file=tmp_path / ".env",
                 output_fn=out.append)
    assert any("PC hub on" in m for m in out)
    assert not any("TRUSTED_PROXY" in m for m in out)
    out.clear()
    cfg["webapp"]["trusted_proxy"] = "127.0.0.1"
    caplog.set_level(logging.WARNING)
    webapp.serve(cfg, host="127.0.0.1", port=8765, env_file=tmp_path / ".env",
                 output_fn=out.append)
    assert any("PC hub is disabled while TRUSTED_PROXY is set" in m for m in out)
    assert not any("PC hub on" in m for m in out)
    assert "PC hub is disabled" in caplog.text
