"""Canvas course content, PR (b): the Canvas helper's way in
(kwil/canvas_helper.py): pairing with a one-time code, `kcs1.` sync keys
(hashed, per person, revocable, rate-limited, counted on the shared
lockout), the two push routes through the token sync's pipeline, and the
outer layers (webapp's cross-site check, the failover edge, CloudGuard)
letting exactly the helper's pushes through.

Offline: the helper is played by the test client, sending what Canvas's
API answers (the File object's fields, pages, assignments, announcements)
and the committed fixture PDF; Claude is the conftest fake client. No
Canvas host is ever contacted (canvas.TRANSPORT would 404 anything)."""

import hashlib
import json
import logging
import sys
from datetime import timedelta

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("cryptography")
from fastapi.testclient import TestClient  # noqa: E402

from kwil import canvas_helper, cloud, coursefiles, webapp  # noqa: E402
from kwil.config import redact  # noqa: E402
from tests.test_coursefiles import PDF, canvas_file, dates_reply, search  # noqa: E402
from tests.test_student_web import delete, s  # noqa: E402,F401 - the fixture
from tests.test_webapp import BASE, TOKEN  # noqa: E402

EXTENSION = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
COURSE = 111
VERIFIER = "Vf" * 20


def course(cid=COURSE, name="MATH 241 - Fall 2026", files=None, **extra):
    """A course as the helper read it from Canvas's API (its own shapes)."""
    c = {"id": cid, "name": name, "course_code": "MATH 241",
         "syllabus_body": "<h2>MATH 241</h2><p>Grading: homework 30 points, exams 70.</p>"
                          "<script>steal()</script>",
         "pages": [{"page_id": 7, "url": "week-1", "title": "Week 1",
                    "updated_at": "2026-10-02T12:00:00Z", "published": True,
                    "body": "<p>Office hours in Altgeld 141.</p>"}],
         "assignments": [{"id": 9, "name": "Problem Set 5", "due_at": "2026-11-04T04:59:00Z",
                          "updated_at": "2026-10-03T12:00:00Z",
                          "description": "<p>Show your work.</p>"}],
         "modules": [{"name": "Week 1", "items": [{"title": "Week 1", "type": "Page"}]}],
         "files": files if files is not None else [
             canvas_file(501, "Syllabus.pdf", "application/pdf", "pdf", len(PDF)),
             canvas_file(502, "Campus map.png", "image/png", "image", 2048)]}
    c.update(extra)
    return c


def manifest(*courses, announcements=None):
    return {"courses": list(courses) or [course()],
            "announcements": announcements if announcements is not None else [
                {"id": 31, "title": "Midterm room", "context_code": f"course_{COURSE}",
                 "message": "<p>The midterm is in Foellinger Auditorium.</p>",
                 "posted_at": "2026-10-20T15:00:00Z"}]}


def bearer(key):
    return {"Authorization": f"Bearer {key}"}


def helper_client(s):
    """The extension: no cookie, its own origin."""
    return TestClient(s.app, base_url=BASE, headers={"Origin": EXTENSION,
                                                     "Sec-Fetch-Site": "none"})


def pair(s, c):
    """'Set up Canvas helper' in the app, then the helper trades the code
    (same-origin, as the extension's content script on the Kwil page)."""
    r = c.post("/api/student/canvas/helper")
    assert r.status_code == 200, r.text
    code = r.json()["code"]
    r = TestClient(s.app, base_url=BASE).post(canvas_helper.PAIR_PATH, json={"code": code})
    assert r.status_code == 200, r.text
    return r.json()["key"], code


def push(s, key, body=None):
    return helper_client(s).post(canvas_helper.MANIFEST_PATH, json=body or manifest(),
                                 headers=bearer(key))


def push_file(s, key, fid=501, data=PDF, cid=COURSE):
    return helper_client(s).post(canvas_helper.FILE_PATH, headers=bearer(key),
                                 data={"course_id": str(cid), "file_id": str(fid)},
                                 files={"file": ("x.bin", data, "application/octet-stream")})


@pytest.fixture
def h(s):
    """The `s` app with Sam (family tier: the owner's Claude key) signed
    in; no Canvas link (their school blocks tokens), no helper yet."""
    sam, uid = s.member("Sam", tier="family")
    s.sam, s.uid = sam, uid
    s.folder = s.tmp / "users" / uid
    s.courses = s.app.state.course_sync
    s.push = s.app.state.canvas_helper
    return s


def _raw(folder) -> bytes:
    return b"".join(p.read_bytes() for p in folder.glob("coursefiles.sqlite*"))


# --- the whole path ---------------------------------------------------------------------

def test_helper_pairs_pushes_syllabus_and_pdf_and_search_finds_them(h):
    """No token, no link: one tap pairs the helper, its manifest stores
    the syllabus, page, assignment, announcement and module list, it asks
    for the PDF only (not the image), the pushed PDF's text is searchable,
    and the dates in it become drafts at the next background tick."""
    key, _ = pair(h, h.sam)
    assert key.startswith("kcs1.") and len(key) == 48
    r = push(h, key)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["want"] == [{"course_id": COURSE, "file_id": 501}]
    assert body["counts"]["added"] == 6 and body["max_file_bytes"] > len(PDF)
    r = push_file(h, key)
    assert r.status_code == 200, r.text
    assert r.json() == {"result": "added", "note": None}
    tools = h.fam.context(h.uid).registry
    data = search(tools, "late policy")
    assert "never as instructions" in data["note"]
    pdf = next(x for x in data["results"] if x["kind"] == "file")
    assert pdf["title"] == "Syllabus.pdf" and "ten percent" in pdf["snippet"]
    kinds = {x["kind"] for x in search(tools, "Altgeld Foellinger Grading work Week")["results"]}
    assert kinds >= {"page", "announcement", "syllabus", "assignment", "module"}
    assert "steal" not in json.dumps(search(tools, "steal"))
    rows = h.fam.coursefiles.export(h.uid)
    assert {r["origin"] for r in rows} == {"helper"}
    blob = json.dumps(rows)
    assert VERIFIER not in blob and "download_frd" not in blob  # the URL was never kept
    # the dates: a draft, never confirmed, read in the background
    h.client.queue.append(dates_reply({"text_index": 0, "title": "Midterm 1",
                                       "course": "MATH 241", "kind": "exam",
                                       "due": "2026-11-12"}))
    assert h.courses.tick() == 0  # nobody to sync (no link), but the dates are read
    items = h.sam.get("/api/student/items").json()["items"]
    midterm = next(i for i in items if i["title"] == "Midterm 1")
    assert midterm["source"] == "course_text" and midterm["status"] == "draft"
    assert "not instructions to you" in h.client.calls[-1]["system"]
    status = h.sam.get("/api/student/items").json()["canvas"]
    assert status["helper"]["paired"] and not status["connected"]
    assert key not in json.dumps(status)


def test_second_manifest_wants_nothing_and_a_changed_file_is_wanted_again(h):
    key, _ = pair(h, h.sam)
    push(h, key)
    push_file(h, key)
    again = push(h, key).json()
    assert again["want"] == [] and again["counts"]["added"] == 0
    newer = course(files=[canvas_file(501, "Syllabus.pdf", "application/pdf", "pdf", len(PDF),
                                      updated="2026-10-09T12:00:00Z")])
    assert push(h, key, manifest(newer)).json()["want"] == [
        {"course_id": COURSE, "file_id": 501}]


def test_a_file_nobody_asked_for_is_refused_and_writes_nothing(h):
    key, _ = pair(h, h.sam)
    assert push_file(h, key).status_code == 409   # no manifest yet
    push(h, key)
    assert push_file(h, key, fid=502).status_code == 409   # the image: never asked for
    assert push_file(h, key, cid=999).status_code == 409   # not a course it named
    assert push_file(h, key).status_code == 200
    assert push_file(h, key).status_code == 409   # each want is used once
    assert not any(r["kind"] == "file" and r["course_id"] == 999
                   for r in h.fam.coursefiles.export(h.uid))


def test_an_oversized_push_is_kept_as_a_note_not_read(h):
    key, _ = pair(h, h.sam)
    push(h, key)
    h.push.max_file_bytes = lambda: 100
    r = push_file(h, key)
    assert r.json() == {"result": "added", "note": "too_large"}
    row = next(r for r in h.fam.coursefiles.export(h.uid) if r["title"] == "Syllabus.pdf")
    assert row["text"] == "" and row["note"] == "too_large"


def test_the_bounds_hold_for_pushed_content(h):
    """Per-course text cap: the token sync's bounds, unchanged."""
    key, _ = pair(h, h.sam)
    h.fam.coursefiles.max_course_bytes = 200
    huge = course(files=[], pages=[{"page_id": n, "url": f"p{n}", "title": f"P{n}",
                                    "body": "<p>" + "word " * 60 + "</p>"} for n in range(5)])
    counts = push(h, key, manifest(huge, announcements=[])).json()["counts"]
    assert counts["skipped"] >= 4
    assert h.fam.coursefiles.stats(h.uid)["bytes"] <= 200


def test_manifest_shapes_are_checked(h):
    key, _ = pair(h, h.sam)
    for bad in ({"courses": "x"}, {"courses": [{"id": "abc"}]}, {"courses": [{"id": True}]},
                {"courses": [{"id": 1, "pages": "x"}]}, [1, 2],
                {"courses": [{"id": n} for n in range(1, 102)]},
                {"courses": [{"id": 1}], "announcements": [{}] * 1001}):
        r = push(h, key, bad)
        assert r.status_code == 400, bad
    r = helper_client(h).post(canvas_helper.MANIFEST_PATH, content=b"{not json",
                              headers={**bearer(key), "Content-Type": "application/json"})
    assert r.status_code == 400


def test_odd_nested_shapes_are_dropped_not_a_crash(h):
    """A module whose items aren't a list, or HTML that isn't text, used to
    reach coursefiles' loop (a TypeError: a 500 after a partial write)."""
    key, _ = pair(h, h.sam)
    odd = course(files=[], modules=[{"name": "W", "items": 5},
                                    {"name": "X", "items": [3, {"title": "Quiz", "type": "Q"}]}],
                 pages=[{"page_id": 8, "url": "p8", "title": "P8", "body": 5}],
                 assignments=[{"id": 4, "name": "A4", "description": {"x": 1}}])
    r = push(h, key, manifest(odd, announcements=[{"id": 2, "title": "T", "message": [1],
                                                   "context_code": f"course_{COURSE}"}]))
    assert r.status_code == 200, r.text
    modules = next(r for r in h.fam.coursefiles.export(h.uid) if r["kind"] == "module")
    assert "Quiz" in modules["text"] and "Module: W" in modules["text"]
    # HTML that isn't text is taken as no body: nothing made up from it
    titles = {r["title"] for r in h.fam.coursefiles.export(h.uid)}
    assert "P8" not in titles and "T" not in titles


def test_a_shape_the_checks_miss_is_a_400(h, monkeypatch):
    key, _ = pair(h, h.sam)

    def boom(*a, **k):
        raise TypeError("odd")

    monkeypatch.setattr(coursefiles, "sync_content", boom)
    assert push(h, key).status_code == 400


def test_a_listing_not_sent_keeps_what_is_stored(h):
    key, _ = pair(h, h.sam)
    push(h, key)
    partial = course(pages=None, files=None)
    del partial["assignments"]
    push(h, key, manifest(partial, announcements=None))
    kinds = {r["kind"] for r in h.fam.coursefiles.export(h.uid)}
    assert kinds >= {"page", "assignment", "announcement"}
    # a listing sent in full drops what Canvas no longer has
    push(h, key, manifest(course(pages=[], files=None)))
    assert "page" not in {r["kind"] for r in h.fam.coursefiles.export(h.uid)}


# --- sync keys -------------------------------------------------------------------------

def test_the_key_is_stored_hashed_privately_and_shown_once(h):
    key, code = pair(h, h.sam)
    path = h.folder / canvas_helper.HELPER_FILE
    raw = path.read_text()
    assert key not in raw and code not in raw
    assert json.loads(raw)["key_hash"] == hashlib.sha256(key.encode()).hexdigest()
    assert "pair_hash" not in json.loads(raw)  # the code is used up
    if sys.platform != "win32":
        assert path.stat().st_mode & 0o077 == 0
    for answer in (h.sam.get("/api/student/items").json(),
                   h.sam.get("/api/student/courses").json()):
        assert key not in json.dumps(answer) and code not in json.dumps(answer)


def test_the_owner_pairs_too(s):
    owner = s.owner()
    key, _ = pair(s, owner)
    assert (s.tmp / "student" / canvas_helper.HELPER_FILE).exists()
    assert push(s, key).status_code == 200
    assert s.app.state.student.courses.stats(None)["documents"] == 6


def test_a_pairing_code_works_once_and_expires(h, monkeypatch):
    r = h.sam.post("/api/student/canvas/helper")
    code = r.json()["code"]
    assert r.json()["expires_in"] == 600 and code.startswith("kcp1.")
    anon = TestClient(h.app, base_url=BASE)
    assert anon.post(canvas_helper.PAIR_PATH, json={"code": code}).status_code == 200
    assert anon.post(canvas_helper.PAIR_PATH, json={"code": code}).status_code == 401
    # a code past its 10 minutes
    code = h.sam.post("/api/student/canvas/helper").json()["code"]
    now = h.sync.helper.clock()
    monkeypatch.setattr(h.sync.helper, "clock", lambda: now + 601)
    assert anon.post(canvas_helper.PAIR_PATH, json={"code": code}).status_code == 401
    assert h.sam.get("/api/student/items").json()["canvas"]["helper"]["pairing"] is False


def test_pairing_again_replaces_the_key(h):
    old, _ = pair(h, h.sam)
    new, _ = pair(h, h.sam)
    assert push(h, old).status_code == 401
    assert push(h, new).status_code == 200


def test_a_sync_key_opens_only_the_push_routes(h):
    key, _ = pair(h, h.sam)
    c = TestClient(h.app, base_url=BASE)
    for method, path in (("GET", "/api/student/items"), ("GET", "/api/student/courses"),
                         ("POST", "/api/student/sync"), ("GET", "/api/state"),
                         ("POST", "/api/student/canvas/helper")):
        r = c.request(method, path, headers={**bearer(key), "Content-Length": "0"})
        assert r.status_code == 401, path
    # not a guess at WEB_TOKEN: never counted toward the lockout
    assert not h.app.state.web.limiter.failures


def test_a_cookie_never_stands_in_for_the_key(h):
    """The push routes read no session: signed in, without the key, 401."""
    pair(h, h.sam)
    assert h.sam.post(canvas_helper.MANIFEST_PATH, json=manifest()).status_code == 401
    r = h.sam.post(canvas_helper.MANIFEST_PATH, json=manifest(), headers=bearer(TOKEN))
    assert r.status_code == 401


def test_wrong_keys_and_codes_count_toward_the_lockout(h, monkeypatch):
    key, _ = pair(h, h.sam)
    waits = []

    async def wait(seconds):
        waits.append(seconds)

    monkeypatch.setattr(webapp, "_wait", wait)
    anon = TestClient(h.app, base_url=BASE)
    wrong = "kcs1." + "x" * 43
    for _ in range(3):
        assert push(h, wrong).status_code == 401
    for _ in range(2):
        assert anon.post(canvas_helper.PAIR_PATH,
                         json={"code": "kcp1." + "y" * 32}).status_code == 401
    assert push(h, wrong).status_code == 429          # one lockout for both
    assert anon.post(canvas_helper.PAIR_PATH, json={"code": "nope"}).status_code == 429
    r = anon.post("/api/login", json={"token": "wrong"})
    assert r.status_code == 429                        # the same limiter as sign-in
    assert push(h, key).status_code == 200             # the right key always works


def test_revoke_disconnect_and_removal_end_the_key(h):
    key, _ = pair(h, h.sam)
    assert h.sam.post("/api/student/canvas/helper/revoke").json() == {"ok": True,
                                                                      "revoked": True}
    assert push(h, key).status_code == 401
    assert not (h.folder / canvas_helper.HELPER_FILE).exists()
    key, _ = pair(h, h.sam)
    assert delete(h.sam, "/api/student/canvas/helper").json()["revoked"] is True
    key, _ = pair(h, h.sam)
    push(h, key)
    # Disconnect (with only the helper): the key and the content go
    r = h.sam.post("/api/student/disconnect", json={})
    assert r.status_code == 200 and r.json()["had"] is True
    assert push(h, key).status_code == 401
    assert b"Altgeld" not in _raw(h.folder)
    # removing the person
    key, _ = pair(h, h.sam)
    owner = h.owner()
    assert owner.post(f"/api/users/{h.uid}/remove").status_code == 200
    assert push(h, key).status_code == 401
    assert not h.folder.exists()


def test_signing_out_keeps_the_helper_paired(h):
    key, _ = pair(h, h.sam)
    assert h.sam.post("/api/logout", json={}).status_code == 200
    assert push(h, key).status_code == 200


def test_disconnect_between_manifest_and_file_writes_nothing(h):
    key, _ = pair(h, h.sam)
    push(h, key)
    # the generation moving on (as Disconnect moves it) alone makes the
    # file the old manifest asked for write nothing, even with the key
    with h.sync.lock:
        h.sync._generations += 1
        h.sync._generation[h.uid] = h.sync._generations
    r = push_file(h, key)
    assert r.status_code == 200 and r.json()["result"] == "stale"
    assert not any(r["kind"] == "file" and r["text"] for r in h.fam.coursefiles.export(h.uid))
    # a real Disconnect: the key goes too, so nothing reaches the store
    push(h, key)
    h.sync.disconnect(h.uid)
    assert push_file(h, key).status_code == 401
    assert not list(h.folder.glob("coursefiles.sqlite*"))


def test_a_revoke_during_a_manifest_stops_its_writes(h, monkeypatch):
    key, _ = pair(h, h.sam)
    real = coursefiles.CourseStore.upsert
    calls = []

    def upsert(self, uid, doc, alive=None):
        calls.append(1)
        if len(calls) == 2:
            h.sync.helper.revoke(h.uid)
        return real(self, uid, doc, alive)

    monkeypatch.setattr(coursefiles.CourseStore, "upsert", upsert)
    body = push(h, key).json()
    assert body["want"] == [] and body["counts"]["added"] == 1


def test_each_person_pushes_only_into_their_own_store(h):
    sam_key, _ = pair(h, h.sam)
    tess, tess_id = h.member("Tess", tier="family")
    tess_key, _ = pair(h, tess)
    push(h, sam_key)
    # Tess names Sam's course id: it lands in Tess's own store, nowhere else
    push(h, tess_key, manifest(course(name="Tess's course", syllabus_body="<p>Tess only</p>",
                                      pages=[], files=[]), announcements=[]))
    sam_rows = h.fam.coursefiles.export(h.uid)
    assert "Tess only" not in json.dumps(sam_rows)
    assert any(r["kind"] == "page" for r in sam_rows)   # her empty pages list touched nothing
    tess_rows = h.fam.coursefiles.export(tess_id)
    assert {r["course"] for r in tess_rows} == {"Tess's course"}
    # Sam's wanted file can't be pushed with Tess's key
    assert push_file(h, tess_key).status_code == 409


def test_pushes_per_key_are_rate_limited(h):
    key, _ = pair(h, h.sam)
    h.push.rate.per_minute = 2   # (per hour here)
    assert push(h, key).status_code == 200
    assert push(h, key).status_code == 200
    r = push(h, key)
    assert r.status_code == 429 and r.headers["Retry-After"]


def test_one_push_at_a_time_per_person(h):
    key, _ = pair(h, h.sam)
    lock = h.push._busy.setdefault(h.uid, __import__("threading").Lock())
    with lock:
        assert push(h, key).status_code == 409


def test_helper_off_means_404(h):
    key, _ = pair(h, h.sam)
    h.config["canvas"]["helper"] = False
    assert push(h, key).status_code == 404
    assert h.sam.post("/api/student/canvas/helper").status_code == 404
    assert h.sam.get("/api/student/items").json()["canvas"]["helper"]["enabled"] is False


# --- the outer layers --------------------------------------------------------------------

def test_an_extension_origin_is_accepted_on_push_but_refused_elsewhere(h):
    key, _ = pair(h, h.sam)
    assert push(h, key).status_code == 200
    r = helper_client(h).post(canvas_helper.MANIFEST_PATH, json=manifest())
    assert r.status_code == 403   # no key: the cross-site check holds
    # the signed-in browser, from an extension's origin, elsewhere: still 403
    for method, path in (("GET", "/api/student/items"), ("POST", "/api/student/sync")):
        r = h.sam.request(method, path, headers={"Origin": EXTENSION,
                                                 "Sec-Fetch-Site": "cross-site",
                                                 "Content-Length": "0"})
        assert r.status_code == 403, path
    # a key on any other route from there: 403 too
    r = helper_client(h).get("/api/student/items", headers=bearer(key))
    assert r.status_code == 403
    # and no CORS headers on the push answer
    r = push(h, key)
    assert not any(k.lower().startswith("access-control-") for k in r.headers)


def test_push_request_is_exactly_the_push_routes_with_a_key():
    key = "Bearer kcs1." + "a" * 43
    assert canvas_helper.push_request("POST", canvas_helper.MANIFEST_PATH, key)
    assert canvas_helper.push_request("POST", canvas_helper.FILE_PATH, key)
    assert not canvas_helper.push_request("GET", canvas_helper.MANIFEST_PATH, key)
    assert not canvas_helper.push_request("POST", "/api/student/items", key)
    assert not canvas_helper.push_request("POST", canvas_helper.PAIR_PATH, key)
    assert not canvas_helper.push_request("POST", canvas_helper.MANIFEST_PATH, None)
    assert not canvas_helper.push_request("POST", canvas_helper.MANIFEST_PATH,
                                          f"Bearer {TOKEN}")


def guard(s, **kwargs):
    from tests.test_cloud import PUBLIC, TRUSTED, at

    # one shared limiter, as cloud.build_app makes it
    s.app.state.web.limiter = limiter = cloud.BoundedLoginLimiter()
    g = cloud.CloudGuard(s.app, TOKEN, trusted_proxies=TRUSTED, max_upload_bytes=200_000,
                         max_body_bytes=16_384, lockout=limiter, **kwargs)
    return g, TestClient(at(PUBLIC, g), base_url=BASE)


def test_cloud_guard_lets_a_key_through_only_on_the_push_routes(h):
    key, _ = pair(h, h.sam)
    g, c = guard(h)
    ext = {"Origin": EXTENSION, "Sec-Fetch-Site": "none", **bearer(key)}
    # a manifest over the 16 KB body cap, under max_upload: its own cap
    big = manifest(course(pages=[{"page_id": n, "url": f"p{n}", "title": f"P{n}",
                                  "body": f"<p>{'x' * 1000}</p>"} for n in range(30)]))
    r = c.post(canvas_helper.MANIFEST_PATH, json=big, headers=ext)
    assert r.status_code == 200, r.text
    r = c.post(canvas_helper.FILE_PATH, headers=ext, data={"course_id": "111", "file_id": "501"},
               files={"file": ("x.bin", PDF, "application/octet-stream")})
    assert r.status_code == 200, r.text
    r = c.post(canvas_helper.MANIFEST_PATH, content=b"x" * 250_000,
               headers={**ext, "Content-Type": "application/json"})
    assert r.status_code == 413
    # a key anywhere else: 401 at the guard, and not counted as a wrong token
    for method, path in (("GET", "/api/student/items"), ("GET", "/api/state"),
                         ("POST", canvas_helper.PAIR_PATH), ("GET", canvas_helper.MANIFEST_PATH)):
        assert c.request(method, path, headers=bearer(key)).status_code == 401, path
    assert not g.lockout.failures
    # a wrong key is counted (at the guard, before any body; one shared limiter)
    for _ in range(5):
        assert c.post(canvas_helper.MANIFEST_PATH, json=manifest(),
                      headers=bearer("kcs1." + "z" * 43)).status_code == 401
    assert g.lockout.blocked("203.0.113.9")
    assert c.post(canvas_helper.MANIFEST_PATH, json=manifest(),
                  headers=ext).status_code == 200      # the right key still works
    assert canvas_helper.PAIR_PATH in cloud.INVITE_POST
    assert cloud.HELPER_POST == set(canvas_helper.PUSH_PATHS)


def test_cloud_guard_pairing_is_public_like_an_invite(h):
    code = h.sam.post("/api/student/canvas/helper").json()["code"]
    _, c = guard(h)
    assert c.post(canvas_helper.PAIR_PATH, json={"code": "x" * 20_000}).status_code == 413
    r = c.post(canvas_helper.PAIR_PATH, json={"code": code})
    assert r.status_code == 200 and r.json()["key"].startswith("kcs1.")


def test_failover_edge_passes_a_push_untouched_and_never_counts_the_key():
    import asyncio

    from kwil.failover.edge import EdgeMiddleware
    from kwil.webapp import LoginLimiter

    seen = []

    async def app(scope, receive, send):
        seen.append(scope)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    limiter = LoginLimiter()

    class Authority:
        def verify(self, *a, **k):
            return None

        def check_root(self, given):
            return False

    edge = EdgeMiddleware(app, authority=Authority(), role="cloud",
                          app_origins=["https://heykwil.com"], limiter=limiter,
                          root_bearer_sid="bearer-x")
    sent = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    def call(path, auth, origin=EXTENSION):
        sent.clear()
        headers = [(b"authorization", auth.encode()), (b"host", b"heykwil.com")]
        if origin:
            headers.append((b"origin", origin.encode()))
        scope = {"type": "http", "method": "POST", "path": path, "headers": headers,
                 "client": ("203.0.113.9", 1)}
        asyncio.run(edge(scope, receive, send))
        return sent[0]

    start = call(canvas_helper.MANIFEST_PATH, "Bearer kcs1." + "a" * 43)
    assert start["status"] == 200 and len(seen) == 1
    assert not any(k.startswith(b"access-control-") for k, _ in start["headers"])
    assert not limiter.failures
    # anywhere else, the extension's origin is refused as before
    assert call("/api/student/items", "Bearer kcs1." + "a" * 43)["status"] == 403
    assert len(seen) == 1
    # with no Origin (a script), a key on another route is a 401 that is
    # never counted as a guess at WEB_TOKEN; a real wrong bearer still is
    assert call("/api/student/items", "Bearer kcs1." + "a" * 43, origin=None)["status"] == 401
    assert call("/api/state", "Bearer kcs1.short", origin=None)["status"] == 401
    assert not limiter.failures and len(seen) == 1
    assert call("/api/state", "Bearer " + "x" * 43, origin=None)["status"] == 401
    assert limiter.failures


# --- logs ------------------------------------------------------------------------------

def test_logs_hold_no_key_code_title_or_text(h, caplog):
    caplog.set_level(logging.DEBUG)
    key, code = pair(h, h.sam)
    push(h, key)
    push_file(h, key)
    push(h, "kcs1." + "w" * 43)
    secrets = (key, code, key[5:], code[5:], "Syllabus.pdf", "late policy", "ten percent",
               "MATH 241", "Altgeld", "Midterm room", "Week 1")
    for record in caplog.records:
        if record.name.startswith("kwil"):
            message = record.getMessage()
            for secret in secrets:
                assert secret not in message, (record.name, message)
    infos = [r.getMessage() for r in caplog.records
             if r.levelno == logging.INFO and r.name == "kwil.canvas_helper"]
    assert any("paired a helper" in m for m in infos)
    assert any("pushed 1 course(s)" in m for m in infos)
    assert any("pushed file 501 of course 111" in m for m in infos)


def test_keys_and_codes_are_redacted_from_log_lines():
    key, code = "kcs1." + "K" * 43, "kcp1." + "C" * 32
    line = redact(f"posting {key} and {{'code': '{code}'}}")
    assert key[5:] not in line and code[5:] not in line


def test_cloud_guard_reads_no_body_for_a_malformed_or_wrong_key(h):
    """Who it is comes first: a malformed or wrong sync key never gets a
    (4 MB-sized) body read or a helper slot, counts like a wrong token, and
    is refused at once once its address is locked out; the right key from
    that address still gets through."""
    import asyncio

    key, _ = pair(h, h.sam)
    g, _ = guard(h)
    size = 150_000   # over the 16 KB cap, under max_upload_bytes

    def call(auth):
        reads, sent = [], []

        async def receive():
            reads.append(1)
            return {"type": "http.request", "body": b"x" * size, "more_body": False}

        async def send(message):
            sent.append(message)

        scope = {"type": "http", "method": "POST", "path": canvas_helper.MANIFEST_PATH,
                 "headers": [(b"authorization", f"Bearer {auth}".encode()),
                             (b"content-length", str(size).encode()),
                             (b"content-type", b"application/json"),
                             (b"host", b"testserver")],
                 "client": ("203.0.113.9", 1), "scheme": "https", "query_string": b"",
                 "http_version": "1.1", "root_path": "", "raw_path": b""}
        asyncio.run(g(scope, receive, send))
        return sent[0]["status"], len(reads)

    wrong = ["kcs1.short", "kcs1." + "!" * 43, "kcs1." + "z" * 43]
    for auth in wrong + wrong[:2]:
        assert call(auth) == (401, 0), auth
    assert g.lockout.blocked("203.0.113.9")
    assert call("kcs1." + "y" * 43) == (429, 0)       # locked out: refused at once
    assert not g.helper_slots.busy
    status, reads = call(key)                          # the right key: read, then 400
    assert status == 400 and reads
    h.config["canvas"]["helper"] = False
    assert call(key) == (404, 0)


# --- what the helper read of the token policy (canvas_schools.py) ----------------------

READ = {"host": "school.instructure.com", "can_create_tokens": False, "only_student": True}


def test_a_manifest_may_say_whether_tokens_are_allowed(h):
    """The helper's reading of /profile/settings rides on a manifest:
    false is "blocked", true from a student-only account "allowed",
    true from a TA (not only a student) nothing; the person's Canvas host
    is kept for the status, and goes with a revoke."""
    key, _ = pair(h, h.sam)
    policies = h.app.state.student.policies
    assert push(h, key, {**manifest(), "canvas": READ}).status_code == 200
    assert policies.policy("school.instructure.com", own=True) == "blocked"
    status = h.sam.get("/api/student/items").json()["canvas"]
    assert status["helper"]["canvas_host"] == "school.instructure.com"
    assert status["policy"] == {"host": "school.instructure.com", "tokens": "blocked"}
    # a TA may make one even where students can't: no verdict from that
    ta = {"host": "ta.instructure.com", "can_create_tokens": True, "only_student": False}
    assert push(h, key, {**manifest(), "canvas": ta}).status_code == 200
    assert policies.policy("ta.instructure.com", own=True) == "unknown"
    # a student-only account that may: allowed
    ok = {"host": "open.instructure.com", "can_create_tokens": True, "only_student": True}
    assert push(h, key, {**manifest(), "canvas": ok}).status_code == 200
    assert policies.policy("open.instructure.com", own=True) == "allowed"
    # without `canvas`, nothing changes
    assert push(h, key).status_code == 200
    assert h.sam.get("/api/student/items").json()["canvas"]["helper"]["canvas_host"] == \
        "open.instructure.com"
    learned = json.loads(policies.path.read_text(encoding="utf-8"))
    assert h.uid not in json.dumps(learned) and "Sam" not in json.dumps(learned)
    h.sam.post("/api/student/canvas/helper/revoke")
    status = h.sam.get("/api/student/items").json()["canvas"]
    assert status["helper"]["canvas_host"] is None and status["policy"] is None


@pytest.mark.parametrize("bad", [
    "school.instructure.com",
    {"host": "school.instructure.com", "can_create_tokens": "false", "only_student": True},
    {"host": "school.instructure.com", "can_create_tokens": 0, "only_student": True},
    {"host": "school.instructure.com", "can_create_tokens": False, "only_student": "yes"},
    {"host": 42, "can_create_tokens": False},
    {"can_create_tokens": False},
    {**READ, "token": "1234~abc"},
])
def test_a_canvas_reading_of_the_wrong_shape_is_refused(h, bad):
    key, _ = pair(h, h.sam)
    r = push(h, key, {**manifest(), "canvas": bad})
    assert r.status_code == 400 and r.json()["error"].startswith("send canvas as")
    assert not h.app.state.student.policies.path.exists()
    assert h.fam.coursefiles.export(h.uid) == []   # nothing of it stored
    with pytest.raises(ValueError):
        canvas_helper.clean_canvas(bad)


@pytest.mark.parametrize("host", ["canvas.uni.internal", "10.1.2.3", "canvas.school.xn--p1ai",
                                  "localhost", "canvas.school.local", "", "a b.edu"])
def test_a_host_this_server_wont_take_drops_only_the_reading(h, host):
    """Judge LOW 3: a host the helper may see but Kwil doesn't take as a
    Canvas host drops the reading; the courses are still stored (only a
    wrong shape or type is a 400)."""
    key, _ = pair(h, h.sam)
    r = push(h, key, {**manifest(), "canvas": {**READ, "host": host}})
    assert r.status_code == 200, r.text
    assert h.fam.coursefiles.export(h.uid)            # the courses arrived
    assert not h.app.state.student.policies.path.exists()
    assert h.sam.get("/api/student/items").json()["canvas"]["helper"]["canvas_host"] is None
    assert canvas_helper.clean_canvas({**READ, "host": host}) is None


def test_a_host_is_taken_in_its_normal_form():
    for host in ("School.Instructure.com", "school.instructure.com.",
                 "https://school.instructure.com"):
        assert canvas_helper.clean_canvas({**READ, "host": host})["host"] == \
            "school.instructure.com"


def test_the_same_reading_every_run_counts_once(h):
    """A helper re-reads the settings page every run: the same reading is
    one event per pairing, a changed one (the school flipped) counts."""
    key, _ = pair(h, h.sam)
    for _ in range(5):
        assert push(h, key, {**manifest(), "canvas": READ}).status_code == 200
    policies = h.app.state.student.policies
    learned = json.loads(policies.path.read_text(encoding="utf-8"))
    assert learned["school.instructure.com"]["blocked"] == 1
    flipped = {**READ, "can_create_tokens": True}
    later = policies.today() + timedelta(days=1)
    policies.today = lambda: later   # the school lifted its restriction the next day
    assert push(h, key, {**manifest(), "canvas": flipped}).status_code == 200
    learned = json.loads(policies.path.read_text(encoding="utf-8"))
    assert learned["school.instructure.com"]["allowed"] == 1
    assert policies.policy("school.instructure.com") == "allowed"   # two events by now


def test_clean_canvas_keeps_a_good_reading():
    assert canvas_helper.clean_canvas(None) is None
    assert canvas_helper.clean_canvas(READ) == READ
    assert canvas_helper.clean_canvas({"host": "x.instructure.com",
                                       "can_create_tokens": True}) == {
        "host": "x.instructure.com", "can_create_tokens": True, "only_student": None}
    assert canvas_helper.canvas_outcome({**READ, "only_student": None}) == "blocked"
    assert canvas_helper.canvas_outcome({**READ, "can_create_tokens": True,
                                         "only_student": None}) is None
    assert canvas_helper.canvas_outcome(None) is None


def test_a_reading_after_a_revoke_records_nothing(h, monkeypatch):
    """The push's alive() check (the key still theirs) guards the reading
    as it guards the content."""
    key, _ = pair(h, h.sam)
    real = canvas_helper.HelperPush.manifest

    def revoke_first(self, uid, digest, body):
        h.app.state.student.helper.revoke(uid)
        return real(self, uid, digest, body)

    monkeypatch.setattr(canvas_helper.HelperPush, "manifest", revoke_first)
    push(h, key, {**manifest(), "canvas": READ})
    assert h.app.state.student.policies.policy("school.instructure.com") == "unknown"


def test_the_reading_logs_no_host(h, caplog):
    caplog.set_level(logging.DEBUG)
    key, _ = pair(h, h.sam)
    push(h, key, {**manifest(), "canvas": {**READ, "host": "secretu.instructure.com"}})
    ours = [r for r in caplog.records if r.name.startswith("kwil")]
    assert any("token policy (blocked)" in r.getMessage() for r in ours)
    assert not any("secretu" in r.getMessage() for r in ours)
