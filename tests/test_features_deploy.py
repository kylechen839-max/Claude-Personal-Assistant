"""The lecture recorder and tutor routes in all three setups: the local web
app (cookie), a failover backend (EdgeMiddleware: bearer tokens and CORS
from the app origin) and the cloud server (CloudGuard). Plus the history
hooks: quick-capture notes and filed lectures show up in a conversation."""

import copy
import io

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from kwil import cloud, history, webapp, webfeatures  # noqa: E402
from kwil.chat import ClaudeChat  # noqa: E402
from kwil.config import DEFAULTS  # noqa: E402
from kwil.history import HistoryStore, Recorder  # noqa: E402
from kwil.lecture import ChunkedUploads, LectureNotesDB, LecturePipeline  # noqa: E402
from kwil.tools import ToolRegistry  # noqa: E402
from kwil.tts import PrintTTS  # noqa: E402
from tests.conftest import make_response, text_block, tool_use_block  # noqa: E402
from tests.test_cloud import PROXY, PUBLIC, at, via_proxy  # noqa: E402
from tests.test_failover_webapp import APP, CROSS, PI, settings  # noqa: E402
from tests.test_lecture import (NOTES, FakeWhisper, assignments_mock,  # noqa: E402
                                fake_windows, notes_reply)
from tests.test_tutor import photo  # noqa: E402
from tests.test_webapp import TOKEN, FakeTranscriber  # noqa: E402

REAL_FEATURES = webfeatures.Features
CHUNK = b"x" * 300_000  # bigger than the cloud's 16 KB cap for ordinary bodies


def features(config, assistant, fake_client, tmp_path):
    assignments, _ = assignments_mock()
    uploads = ChunkedUploads(tmp_path / "lec")
    pipeline = LecturePipeline(uploads, fake_client, LectureNotesDB(assignments),
                               whisper_factory=lambda name: FakeWhisper())
    pipeline._windows = lambda audio: iter(fake_windows())
    return REAL_FEATURES(config, assistant, client=fake_client,
                         assignments=assignments, pipeline=pipeline)


def make_assistant(fake_client):
    return webapp.WebAssistant(
        lambda reg: ClaudeChat(client=fake_client, model="m", tools=reg),
        FakeTranscriber("hi"), PrintTTS())


def exercise(client, headers, fake_client):
    """Start a lecture, upload a big piece, finish; ask the tutor."""
    r = client.post("/api/lecture/start", json={"course": "PHYS 141"}, headers=headers)
    assert r.status_code == 200, r.text
    uid = r.json()["id"]
    r = client.post(f"/api/lecture/{uid}/chunk?offset=0", content=CHUNK,
                    headers={**headers, "content-type": "application/octet-stream"})
    assert r.status_code == 200, r.text
    assert r.json() == {"received": len(CHUNK)}
    fake_client.queue = [make_response(text_block("Try dividing by $2$."))]
    r = client.post("/api/tutor", data={"question": "2x=6"}, headers=headers,
                    files={"image": ("p.jpg", photo(exif=False), "image/jpeg")})
    assert r.status_code == 200, r.text
    return uid


def refused(client, headers=None):
    headers = headers or {}
    codes = [client.post("/api/lecture/start", json={}, headers=headers).status_code,
             client.post("/api/lecture/abcdefghijklmnopqrstuvwx/chunk?offset=0",
                         content=b"x", headers=headers).status_code,
             client.get("/api/lectures", headers=headers).status_code,
             client.post("/api/tutor", data={"question": "x"},
                         headers=headers).status_code]
    return all(c in (401, 403) for c in codes), codes


# ---------- 1. local web app (cookie) ----------
def test_local_web_app(fake_client, tmp_path):
    assistant = make_assistant(fake_client)
    config = copy.deepcopy(DEFAULTS)
    app = webapp.create_app(assistant, TOKEN, reap_interval=3600,
                            features=features(config, assistant, fake_client, tmp_path))
    client = TestClient(app, base_url="https://testserver")
    ok, codes = refused(client)
    assert ok, codes
    assert client.post("/api/login", json={"token": TOKEN}).status_code == 200
    exercise(client, {}, fake_client)


# ---------- 2. a failover backend (Pi): bearer token, cross-origin ----------
def test_failover_backend(fake_client, tmp_path):
    assistant = make_assistant(fake_client)
    config = copy.deepcopy(DEFAULTS)
    app = webapp.create_app(assistant, TOKEN, reap_interval=3600,
                            failover=settings("pi"),
                            features=features(config, assistant, fake_client, tmp_path))
    client = TestClient(app, base_url=PI)
    ok, codes = refused(client, CROSS)
    assert ok, codes
    ok, codes = refused(client, {**CROSS, "authorization": "Bearer hc1.wrong"})
    assert ok, codes
    token = client.post("/api/auth/token", json={"token": TOKEN},
                        headers=CROSS).json()["access_token"]
    exercise(client, {**CROSS, "authorization": f"Bearer {token}"}, fake_client)
    # the chunk upload is a POST: CORS from the app origin allows it
    pre = client.options("/api/lecture/x/chunk", headers={
        **CROSS, "access-control-request-method": "POST",
        "access-control-request-headers": "authorization,content-type"})
    assert pre.headers.get("access-control-allow-origin") == APP
    assert "POST" in pre.headers.get("access-control-allow-methods", "")


# ---------- 3. the cloud server (CloudGuard, with and without failover) ----------
@pytest.mark.parametrize("role", ["", "cloud"])
def test_cloud_server(fake_client, tmp_path, monkeypatch, role):
    config = copy.deepcopy(DEFAULTS)
    config["cloud"]["trusted_proxies"] = ["10.0.0.0/8"]
    config["webapp"]["sessions_file"] = str(tmp_path / "sessions.json")
    config["webapp"]["max_upload_mb"] = 4  # as in the server profile
    config["failover"].update(enabled=True, role=role, app_origin=APP if role else "",
                              backends=[{"role": "pi", "url": PI}] if role else [])
    assistant = make_assistant(fake_client)
    built = {}

    def fake_features(cfg, web_assistant):
        built["f"] = features(cfg, web_assistant, fake_client, tmp_path)
        return built["f"]
    monkeypatch.setattr(webfeatures, "Features", fake_features)
    guard = cloud.build_app(config, assistant, TOKEN)
    client = TestClient(at(PROXY, guard), base_url=APP, follow_redirects=False)
    hdr = via_proxy(PUBLIC)
    ok, codes = refused(client, hdr)
    assert ok, codes
    # the page code is public (the login page loads it), the API is not
    for path in ("/static/features.js", "/static/katex/katex.min.js"):
        assert client.get(path, headers=hdr).status_code == 200
    assert client.get("/static/katex/fonts/KaTeX_Main-Regular.woff2",
                      headers=hdr).status_code == 401
    if role:
        token = client.post("/api/auth/token", json={"token": TOKEN},
                            headers=hdr).json()["access_token"]
        auth = {**hdr, "authorization": f"Bearer {token}"}
    else:
        assert client.post("/api/login", json={"token": TOKEN},
                           headers=hdr).status_code == 200
        auth = hdr
    exercise(client, auth, fake_client)
    assert built["f"].config["lecture"]["chunk_mb"] <= 3.9  # fits the 4 MB cap


def test_cloud_upload_paths():
    assert cloud.is_upload("/api/lecture/abcdefghijklmnopqrstuvwx/chunk")
    assert cloud.is_upload("/api/tutor") and cloud.is_upload("/api/turn")
    assert not cloud.is_upload("/api/lecture/start")
    assert not cloud.is_upload("/api/lecture/x/finish")


# ---------- history hooks ----------
@pytest.fixture
def store(tmp_path, monkeypatch):
    s = HistoryStore(tmp_path / "h.sqlite")
    monkeypatch.setattr(history, "_STORE", s)
    yield s
    s.close()


def test_saved_note_shows_in_the_conversation(store, fake_client):
    from tests.test_notes import FakeIndex, toy_embed
    from unittest.mock import MagicMock

    from kwil import cli
    from kwil.config import load_config

    memory = MagicMock(namespace="voice-assistant")
    memory.index = FakeIndex()
    memory.embed.side_effect = toy_embed
    reg = ToolRegistry()
    cli.register_notes(reg, memory, load_config(None, None))
    rec = Recorder(store, "text", run_async=False, client_fn=lambda: fake_client)
    chat = ClaudeChat(client=fake_client, model="m", tools=reg, recorder=rec)
    fake_client.queue = [
        make_response(tool_use_block("remember_note",
                                     {"text": "Lab report needs error bars"}),
                      stop_reason="tool_use"),
        make_response(text_block("Got it."))]
    chat.ask("Remember that the lab report needs error bars")
    msgs = store.get(chat.conversation_id)["messages"]
    assert [m["kind"] for m in msgs] == ["chat", "note", "chat"]
    assert msgs[1]["text"] == "[note] Saved a note to long-term memory."
    # the note's text lives only in Pinecone, so delete_note removes it fully
    assert all("error bars" not in m["text"] for m in msgs if m["kind"] == "note")


def test_filed_lecture_is_added_to_its_conversation(store, fake_client, tmp_path):
    cid = store.create("web")
    store.append(cid, "user", "I'm about to record physics")
    assistant = make_assistant(fake_client)
    feats = features(copy.deepcopy(DEFAULTS), assistant, fake_client, tmp_path)
    feats.pipeline.on_filed = webfeatures.lecture_to_history
    uploads = feats.pipeline.uploads
    uid = uploads.start(course="PHYS 141", conversation_id=cid)
    uploads.append(uid, 0, b"audio")
    uploads.finish(uid)
    fake_client.queue = [notes_reply(NOTES)]
    feats.pipeline.submit(uid)
    feats.pipeline.wait(10)
    msgs = store.get(cid)["messages"]
    assert msgs[-1]["kind"] == "note"
    text = msgs[-1]["text"]
    assert text.startswith("[lecture] Lecture notes filed in Notion: Work and kinetic energy")
    assert "PHYS 141" in text and "https://notion.so/page1" in text


def test_lecture_without_a_conversation_is_not_recorded(store, fake_client, tmp_path):
    from types import SimpleNamespace

    job = SimpleNamespace(title="t", course="c", duration_seconds=None, notion_url="u")
    assert webfeatures.lecture_to_history(job, NOTES, {"conversation_id": ""}) is False
