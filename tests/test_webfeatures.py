"""Phone app routes for the lecture recorder and the homework tutor: auth,
uploads with resume, image type/size rejection, the mocked vision call,
and the vendored KaTeX."""

import io
import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from PIL import Image  # noqa: E402

from assistant import webapp, webfeatures  # noqa: E402
from assistant.config import load_config  # noqa: E402
from assistant.lecture import ChunkedUploads, LectureNotesDB, LecturePipeline  # noqa: E402
from tests.conftest import make_response, text_block, tool_use_block  # noqa: E402
from tests.test_lecture import NOTES, FakeWhisper, assignments_mock, fake_windows  # noqa: E402
from tests.test_tutor import photo  # noqa: E402
from tests.test_webapp import login, make_app  # noqa: E402,F401

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def features_app(make_app, fake_client, tmp_path):
    def make(**cfg_changes):
        assistant, client = make_app()
        config = load_config(None, None)
        for section, values in cfg_changes.items():
            config[section].update(values)
        assignments, notion = assignments_mock()
        uploads = ChunkedUploads(tmp_path / "lec", max_bytes=10_000,
                                 max_chunk_bytes=1_000)
        pipeline = LecturePipeline(uploads, fake_client, LectureNotesDB(assignments),
                                   whisper_factory=lambda name: FakeWhisper())
        pipeline._windows = lambda audio: iter(fake_windows())
        feats = webfeatures.Features(config, assistant, client=fake_client,
                                     assignments=assignments, pipeline=pipeline)
        webfeatures.install(client.app, feats)
        return client, feats, notion
    return make


ROUTES = [("get", "/api/lecture/courses"), ("post", "/api/lecture/start"),
          ("get", "/api/lecture/abcdefghijklmnopqrstuvwx"),
          ("put", "/api/lecture/abcdefghijklmnopqrstuvwx/chunk?offset=0"),
          ("post", "/api/lecture/abcdefghijklmnopqrstuvwx/finish"),
          ("post", "/api/lecture/abcdefghijklmnopqrstuvwx/retry"),
          ("post", "/api/lecture/abcdefghijklmnopqrstuvwx/assignments"),
          ("get", "/api/lectures"), ("post", "/api/tutor")]


@pytest.mark.parametrize("method,path", ROUTES)
def test_every_route_needs_sign_in(features_app, method, path):
    client, _, _ = features_app()
    kwargs = {} if method == "get" else {"content": b"{}"}
    assert getattr(client, method)(path, **kwargs).status_code == 401
    client.cookies.set(webapp.COOKIE, "forged.0123")
    assert getattr(client, method)(path, **kwargs).status_code == 401


def test_cross_origin_requests_are_refused(features_app):
    client, _, _ = features_app()
    login(client)
    r = client.post("/api/lecture/start", json={},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


# ---------- lecture ----------
def test_lecture_upload_with_a_lost_chunk_then_notes(features_app, fake_client):
    client, feats, notion = features_app()
    login(client)
    courses = client.get("/api/lecture/courses").json()
    assert courses["courses"] == ["PHYS 141", "CS II"] and courses["chunk_bytes"] > 0
    start = client.post("/api/lecture/start", json={
        "course": "PHYS 141", "mime": "audio/webm",
        "started_at": "2026-10-16T09:00:00-04:00"}).json()
    uid = start["id"]
    put = lambda offset, data: client.put(  # noqa: E731
        f"/api/lecture/{uid}/chunk?offset={offset}", content=data,
        headers={"Content-Type": "application/octet-stream"})
    assert put(0, b"a" * 500).json() == {"received": 500}
    gap = put(1000, b"c" * 500)  # the piece at 500 got lost
    assert gap.status_code == 409 and gap.json()["received"] == 500
    assert client.get(f"/api/lecture/{uid}").json()["received"] == 500
    assert put(500, b"b" * 500).json() == {"received": 1000}
    assert put(500, b"b" * 500).json() == {"received": 1000}  # repeat: harmless
    assert put(1000, b"c" * 500).json() == {"received": 1500}
    assert put(0, b"x" * 1001).status_code == 413  # chunk cap
    fake_client.queue = [make_response(tool_use_block("save_lecture_notes", NOTES),
                                       stop_reason="tool_use")]
    assert client.post(f"/api/lecture/{uid}/finish",
                       json={"total": 2000}).status_code == 409
    job = client.post(f"/api/lecture/{uid}/finish", json={"total": 1500}).json()
    assert job["status"] in ("queued", "transcribing", "summarizing", "saving", "done")
    feats.pipeline.wait(10)
    status = client.get(f"/api/lecture/{uid}").json()
    assert status["finished"] and status["job"]["status"] == "done"
    assert status["job"]["notion_url"] == "https://notion.so/page1"
    jobs = client.get("/api/lectures").json()["jobs"]
    assert jobs[0]["id"] == uid and len(jobs[0]["candidates"]) == 2
    # Kyle adds one suggestion; nothing else is added
    before = notion.pages.create.call_count
    notion.pages.create.side_effect = lambda **kw: {"id": "a1", "properties": {}}
    r = client.post(f"/api/lecture/{uid}/assignments", json={"index": 0})
    assert r.status_code == 200 and r.json()["status"] == "created"
    assert notion.pages.create.call_count == before + 1
    assert client.post(f"/api/lecture/{uid}/assignments",
                       json={"index": 1}).status_code == 400


def test_recording_size_cap(features_app):
    client, _, _ = features_app()
    login(client)
    uid = client.post("/api/lecture/start", json={}).json()["id"]
    for i in range(10):
        client.put(f"/api/lecture/{uid}/chunk?offset={i * 1000}", content=b"x" * 1000)
    r = client.put(f"/api/lecture/{uid}/chunk?offset=10000", content=b"x")
    assert r.status_code == 413


def test_unknown_or_traversal_upload_ids(features_app):
    client, _, _ = features_app()
    login(client)
    for bad in ["nope", "..%2F..%2Fetc", "abcdefghijklmnopqrstuvwx"]:
        assert client.get(f"/api/lecture/{bad}").status_code in (404, 400)


def test_lecture_off_in_config(features_app):
    client, _, _ = features_app(lecture={"enabled": False})
    login(client)
    assert client.post("/api/lecture/start", json={}).status_code == 404


# ---------- tutor ----------
def test_tutor_photo_and_question(features_app, fake_client):
    client, feats, _ = features_app()
    login(client)
    fake_client.queue = [make_response(text_block("Try dividing by $2$.")),
                         make_response(text_block("Yes: $x = 3$."))]
    raw = photo(size=(3000, 2000))  # with EXIF
    r = client.post("/api/tutor", data={"question": "Solve $2x=6$", "speak": "1"},
                    files={"image": ("p.jpg", raw, "image/jpeg")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["text"] == "Try dividing by $2$."
    assert body["spoken"] == "Try dividing by 2."
    assert body["audio"].startswith("/api/audio/")
    sent = fake_client.calls[0]["messages"][0]["content"][0]["source"]
    import base64
    data = base64.b64decode(sent["data"])
    assert b"SecretSoftware" not in data and b"PhoneMaker" not in data
    with Image.open(io.BytesIO(data)) as img:
        assert max(img.size) == 1600 and not img.getexif()
    assert "Socratic" in fake_client.calls[0]["system"]
    # a follow-up without a photo continues the same problem
    r = client.post("/api/tutor", data={"question": "Is it 3?"})
    assert r.json()["text"] == "Yes: $x = 3$." and "audio" not in r.json()
    assert len(fake_client.calls[1]["messages"]) == 3


@pytest.mark.parametrize("name,data,ctype,status", [
    ("p.gif", None, "image/gif", 415),
    ("p.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml", 415),
    ("p.jpg", b"%PDF-1.7 pretending", "image/jpeg", 415),
    ("p.heic", b"....ftypheic", "image/heic", 415),
])
def test_tutor_rejects_other_types(features_app, fake_client, name, data, ctype, status):
    client, _, _ = features_app()
    login(client)
    if data is None:
        buf = io.BytesIO()
        Image.new("RGB", (10, 10)).save(buf, "GIF")
        data = buf.getvalue()
    r = client.post("/api/tutor", data={"question": "x"},
                    files={"image": (name, data, ctype)})
    assert r.status_code == status and fake_client.calls == []


def test_tutor_rejects_big_images(features_app, fake_client):
    client, _, _ = features_app(tutor={"max_image_mb": 0.05})
    login(client)
    noisy = Image.effect_noise((600, 600), 100).convert("RGB")
    buf = io.BytesIO()
    noisy.save(buf, "PNG")
    assert len(buf.getvalue()) > 50_000
    r = client.post("/api/tutor", data={"question": "x"},
                    files={"image": ("p.png", buf.getvalue(), "image/png")})
    assert r.status_code == 413 and fake_client.calls == []


def test_tutor_needs_a_photo_to_start(features_app, fake_client):
    client, _, _ = features_app()
    login(client)
    r = client.post("/api/tutor", data={"question": "help"})
    assert r.status_code == 400 and "photo" in r.json()["error"]


def test_tutor_api_failure_is_generic(features_app, fake_client):
    client, _, _ = features_app()
    login(client)
    fake_client.messages.create.side_effect = RuntimeError("secret detail")
    r = client.post("/api/tutor", data={"question": "x"},
                    files={"image": ("p.jpg", photo(exif=False), "image/jpeg")})
    assert r.status_code == 502 and "secret" not in r.text


# ---------- page code and KaTeX ----------
def test_katex_is_vendored_and_served_without_a_cdn(features_app):
    client, _, _ = features_app()
    page = client.get("/").text
    assert "/static/katex/katex.min.js" in page and "/static/features.js" in page
    assert not re.search(r'(?:src|href)="https?://', page)  # nothing from a CDN
    js = client.get("/static/katex/katex.min.js")
    assert js.status_code == 200 and "katex" in js.text[:2000].lower()
    css = client.get("/static/katex/katex.min.css").text
    fonts = set(re.findall(r"url\((fonts/[^)]+)\)", css))
    assert fonts and all(f.endswith(".woff2") for f in fonts)
    for f in sorted(fonts)[:3]:
        assert client.get(f"/static/katex/{f}").status_code == 200
    assert "font-src 'self'" in js.headers["content-security-policy"]
    assert client.get("/static/features.css").status_code == 200
    assert client.get("/static/../webapp.py").status_code == 404


def test_feature_page_code_has_no_cdn_or_inner_html():
    js = (ROOT / "assistant/web/static/features.js").read_text()
    assert "innerHTML" not in js and "http://" not in js
    assert "katex.render(" in js and "renderToString" not in js  # CSP-safe
    assert "wakeLock" in js and "Ask your professor" in js
    assert "1600" in js


def test_static_files_exist_for_every_katex_font():
    css = (ROOT / "assistant/web/static/katex/katex.min.css").read_text()
    for font in set(re.findall(r"url\((fonts/[^)]+)\)", css)):
        assert (ROOT / "assistant/web/static/katex" / font).exists(), font



def test_busy_server_answers_429_not_500(features_app):
    from assistant.lecture import UploadBusy

    client, feats, _ = features_app()
    login(client)

    def busy(*a, **k):
        raise UploadBusy("Too many unfinished recordings")
    feats.pipeline.uploads.start = busy
    r = client.post("/api/lecture/start", json={})
    assert r.status_code == 429 and "unfinished" in r.json()["error"]


def test_lecture_off_blocks_every_lecture_route(features_app):
    client, _, _ = features_app(lecture={"enabled": False})
    login(client)
    uid = "a" * 24
    assert client.put(f"/api/lecture/{uid}/chunk?offset=0", content=b"x").status_code == 404
    assert client.post(f"/api/lecture/{uid}/finish", json={}).status_code == 404
    assert client.post(f"/api/lecture/{uid}/retry", json={}).status_code == 404


def test_limits_are_lowered_to_fit_the_request_cap():
    cfg = load_config(None, None)
    assert webfeatures.check_limits(cfg) == []  # defaults fit
    cfg["lecture"]["chunk_mb"], cfg["tutor"]["max_image_mb"] = 12, 20
    warnings = webfeatures.check_limits(cfg)
    assert len(warnings) == 2 and "chunk_mb" in warnings[0]
    assert cfg["lecture"]["chunk_mb"] == cfg["tutor"]["max_image_mb"] == 9.9
    server = load_config(None, None)
    server["webapp"]["max_upload_mb"] = 4  # the cloud profile
    webfeatures.check_limits(server)
    assert server["lecture"]["chunk_mb"] == 3.9
