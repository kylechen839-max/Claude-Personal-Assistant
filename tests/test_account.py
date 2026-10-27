"""Account self-service (kwil/account.py): a family member downloads
their data (GET /api/me/export) or deletes their account (POST
/api/me/delete), only ever their own, never the owner's.

All offline: the mocked Anthropic client, real SQLite history in tmp_path,
and a fake Google revoke (no network)."""

import gc
import io
import json
import logging
import os
import time
import zipfile

import pytest

pytest.importorskip("fastapi")

from kwil import account as account_mod, webapp  # noqa: E402
from tests.test_family import env, owner  # noqa: E402,F401 (fixtures)
from tests.test_webapp import TOKEN  # noqa: E402

# planted in the member's folder: none may ever reach an export or a log
REFRESH = "1//refresh-SECRET-abc123"
ACCESS = "ya29.access-SECRET-xyz"
KEY_FILES = {"anthropic_key.enc": "sk-ant-SECRET-key", "notion.enc": "ntn_SECRET-token"}


@pytest.fixture
def acct(env, tmp_path):
    """The Account object with a recording fake revoke and the env's clock;
    exports go to its default place, `<family dir>/.exports` (in tmp_path)."""
    a = env.app.state.account
    a.revoked = []
    a.revoke = a.revoked.append
    a.clock = lambda: env.clock.now
    assert a.tmp_root == env.family.dir / ".exports"
    return a


def plant(env, uid, conversations=(("Dentist on Friday", "Remind me about the dentist"),)):
    """Their Google token, key files and some history."""
    folder = env.tmp / "users" / uid
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "google_token.json").write_text(json.dumps({
        "token": ACCESS, "refresh_token": REFRESH, "client_id": "cid",
        "client_secret": "csecret", "scopes": ["https://www.googleapis.com/auth/calendar"],
        "expiry": "2999-01-01T00:00:00Z"}), encoding="utf-8")
    for name, text in KEY_FILES.items():
        (folder / name).write_text(text, encoding="utf-8")
    store = env.family.context(uid).history
    ids = []
    for title, said in conversations:
        cid = store.create("web")
        store.append(cid, "user", said)
        store.append(cid, "assistant", "Sure.")
        store.finish(cid, title, "A summary.")
        ids.append(cid)
    return folder, ids


def delete_me(client, confirm="DELETE"):
    return client.post("/api/me/delete", json={"confirm": confirm})


def unzip(response) -> dict:
    zf = zipfile.ZipFile(io.BytesIO(response.content))
    return {n: zf.read(n).decode("utf-8") for n in zf.namelist()}


# --- delete -----------------------------------------------------------------------

def test_delete_removes_the_whole_folder_and_signs_out_everywhere(env, owner, acct):
    mom, link = env.member(owner, name="Mom")
    uid = link["user"]["id"]
    folder, _ = plant(env, uid)
    # a second phone of theirs
    other_phone = env.client()
    other_phone.cookies.set(webapp.COOKIE, webapp.make_cookie(
        TOKEN, env.app.state.web.store.create(uid), env.app.state.web.store.epoch))
    assert other_phone.get("/api/state").status_code == 200
    r = delete_me(mom)
    assert r.status_code == 200, r.text
    assert not folder.exists()                   # history, token, both key files
    assert uid not in env.family.users
    assert mom.get("/api/state").status_code == 401
    assert other_phone.get("/api/state").status_code == 401
    assert not env.app.state.web.store.revoke_user(uid)
    assert not any(s.user == uid for s in env.assistant.sessions.values())
    # the cookie is cleared
    assert webapp.COOKIE in r.headers["set-cookie"] and "Max-Age=0" in r.headers["set-cookie"]
    assert r.headers.get("clear-site-data") == '"cookies"'
    # the owner is untouched
    assert owner.get("/api/state").status_code == 200


def test_delete_revokes_google_first(env, owner, acct):
    mom, link = env.member(owner)
    uid = link["user"]["id"]
    folder, _ = plant(env, uid)
    seen = []
    acct.revoke = lambda token: seen.append((token, folder.exists()))
    assert delete_me(mom).status_code == 200
    assert seen == [(REFRESH, True)]  # the refresh token, before the folder went


def test_delete_goes_ahead_when_revoke_fails(env, owner, acct, caplog):
    mom, link = env.member(owner)
    uid = link["user"]["id"]
    folder, _ = plant(env, uid)

    def broken(token):
        raise ConnectionError(f"couldn't reach Google with {token}")
    acct.revoke = broken
    caplog.set_level(logging.DEBUG)
    assert delete_me(mom).status_code == 200
    assert not folder.exists()
    assert any("revoking" in r.getMessage() and "ConnectionError" in r.getMessage()
               for r in caplog.records)
    assert REFRESH not in caplog.text and ACCESS not in caplog.text


def test_delete_without_google_skips_revoke(env, owner, acct):
    mom, link = env.member(owner)
    assert delete_me(mom).status_code == 200
    assert acct.revoked == []


def test_owners_remove_revokes_google_first(env, owner, acct, monkeypatch, caplog):
    # the owner's Remove (family.py) revokes Google as "Delete my account"
    # does, through the same helper, before the folder goes
    mom, link = env.member(owner)
    uid = link["user"]["id"]
    folder, _ = plant(env, uid)
    seen = []
    monkeypatch.setattr(account_mod, "revoke_google",
                        lambda token: seen.append((token, folder.exists())))
    caplog.set_level(logging.DEBUG)
    assert owner.post(f"/api/users/{uid}/remove", json={}).status_code == 200
    assert seen == [(REFRESH, True)] and not folder.exists()
    assert uid not in env.family.users
    assert any(uid in r.getMessage() and "revoked" in r.getMessage()
               for r in caplog.records if r.levelno == logging.INFO)
    assert REFRESH not in caplog.text and ACCESS not in caplog.text
    # an unknown id: nothing to revoke, still a 404
    assert owner.post("/api/users/u_nobody12345/remove", json={}).status_code == 404
    assert len(seen) == 1


def test_owners_remove_goes_ahead_when_google_revoke_fails(env, owner, acct,
                                                           monkeypatch, caplog):
    mom, link = env.member(owner)
    uid = link["user"]["id"]
    folder, _ = plant(env, uid)
    calls = []

    def broken(token):
        calls.append(token)
        raise ConnectionError(f"couldn't reach Google with {token}")
    monkeypatch.setattr(account_mod, "revoke_google", broken)
    caplog.set_level(logging.DEBUG)
    assert owner.post(f"/api/users/{uid}/remove", json={}).status_code == 200
    assert calls == [REFRESH]
    assert not folder.exists() and uid not in env.family.users
    assert any("revoking" in r.getMessage() and "ConnectionError" in r.getMessage()
               for r in caplog.records)
    assert REFRESH not in caplog.text and ACCESS not in caplog.text


def test_revoke_google_posts_the_token_in_the_body(monkeypatch):
    calls = []

    class Resp:
        status_code = 200

    import requests
    monkeypatch.setattr(requests, "post",
                        lambda url, **kw: calls.append((url, kw)) or Resp())
    account_mod.revoke_google("tok-1")
    (url, kw), = calls
    assert url == "https://oauth2.googleapis.com/revoke" and "tok-1" not in url
    assert kw["data"] == {"token": "tok-1"} and kw["timeout"]
    Resp.status_code = 400
    with pytest.raises(RuntimeError):
        account_mod.revoke_google("tok-1")


@pytest.mark.parametrize("confirm", [None, "", "delete please", "yes", 123, "Mo"])
def test_delete_needs_the_typed_confirmation(env, owner, acct, confirm):
    mom, link = env.member(owner, name="Mom")
    uid = link["user"]["id"]
    folder, _ = plant(env, uid)
    r = mom.post("/api/me/delete", json={} if confirm is None else {"confirm": confirm})
    assert r.status_code == 400
    assert folder.exists() and uid in env.family.users and acct.revoked == []
    assert mom.get("/api/state").status_code == 200


def test_delete_accepts_their_name_in_any_case(env, owner, acct):
    mom, link = env.member(owner, name="Mom Chen")
    assert delete_me(mom, confirm="  mom   chen ").status_code == 200
    assert link["user"]["id"] not in env.family.users


def test_the_owner_cannot_delete_themselves_here(env, owner, acct):
    mom, link = env.member(owner)
    assert delete_me(owner).status_code == 403
    bearer = env.client()
    r = bearer.post("/api/me/delete", json={"confirm": "DELETE"},
                    headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 403
    assert owner.get("/api/state").status_code == 200
    assert link["user"]["id"] in env.family.users and acct.revoked == []
    anon = env.client()
    assert delete_me(anon).status_code == 401


def test_no_way_to_delete_someone_else(env, owner, acct):
    mom, mlink = env.member(owner, name="Mom")
    dad, dlink = env.member(owner, name="Dad")
    dad_folder, _ = plant(env, dlink["user"]["id"])
    mom_folder, _ = plant(env, mlink["user"]["id"])
    # an id in the body (or the query) is ignored: only the caller's own
    r = mom.post(f"/api/me/delete?id={dlink['user']['id']}",
                 json={"confirm": "DELETE", "id": dlink["user"]["id"],
                       "user": dlink["user"]["id"]})
    assert r.status_code == 200
    assert not mom_folder.exists() and dad_folder.exists()
    assert dlink["user"]["id"] in env.family.users
    assert dad.get("/api/state").status_code == 200
    # Mom's (now revoked) cookie can't delete anyone either
    assert delete_me(mom).status_code == 401
    assert dad_folder.exists()


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_delete_and_export_are_cross_site_protected(env, owner, acct, site):
    mom, link = env.member(owner)
    folder, _ = plant(env, link["user"]["id"])
    r = mom.post("/api/me/delete", json={"confirm": "DELETE"},
                 headers={"Sec-Fetch-Site": site})
    assert r.status_code == 403
    assert mom.get("/api/me/export", headers={"Sec-Fetch-Site": site}).status_code == 403
    r = mom.post("/api/me/delete", json={"confirm": "DELETE"},
                 headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert folder.exists() and acct.revoked == []
    # the same requests from the app's own page work
    assert mom.get("/api/me/export", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200
    r = mom.post("/api/me/delete", json={"confirm": "DELETE"},
                 headers={"Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200 and not folder.exists()


def test_off_without_family(tmp_path):
    from unittest.mock import MagicMock

    from fastapi.testclient import TestClient

    from kwil.tts import PrintTTS
    from tests.test_webapp import BASE, FakeTranscriber, login

    assistant = webapp.WebAssistant(lambda reg: MagicMock(), FakeTranscriber(), PrintTTS())
    app = webapp.create_app(assistant, TOKEN, reap_interval=3600,
                            sessions_file=tmp_path / "s.json")
    c = TestClient(app, base_url=BASE)
    login(c)
    assert c.post("/api/me/delete", json={"confirm": "DELETE"}).status_code == 404
    assert c.get("/api/me/export").status_code == 404


# --- export -----------------------------------------------------------------------

def test_export_has_their_conversations_and_profile(env, owner, acct):
    mom, link = env.member(owner, name="Mom", language="zh", timezone="Asia/Shanghai")
    uid = link["user"]["id"]
    _, ids = plant(env, uid, [("Dentist", "dentist on friday"), ("Groceries", "buy milk")])
    env.family.context(uid).history.update(ids[0], archived=True)  # still theirs
    r = mom.get("/api/me/export")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    assert 'attachment; filename="kwil-data.zip"' == r.headers["content-disposition"]
    files = unzip(r)
    prof = json.loads(files["profile.json"])
    assert prof == {"id": uid, "name": "Mom", "language": "zh",
                    "timezone": "Asia/Shanghai", "tier": "beta",
                    "created": "2023-11-14T22:13:20Z"}
    convs = {n for n in files if n.startswith("conversations/")}
    assert convs == {f"conversations/{cid}.json" for cid in ids}
    one = json.loads(files[f"conversations/{ids[1]}.json"])
    assert one["title"] == "Groceries"
    assert [m["text"] for m in one["messages"]] == ["buy milk", "Sure."]


def test_export_has_no_tokens_or_keys(env, owner, acct):
    mom, link = env.member(owner)
    plant(env, link["user"]["id"])
    files = unzip(mom.get("/api/me/export"))
    assert set(files) == {"profile.json", "deadlines.json", "coursefiles.json"} | {
        n for n in files if n.startswith("conversations/")}
    blob = "\n".join(files.values())
    for secret in (REFRESH, ACCESS, "csecret", "invite_hash", *KEY_FILES.values()):
        assert secret not in blob
    assert not any(n.endswith((".enc", "google_token.json", ".sqlite")) for n in files)


def test_export_never_includes_another_users_data(env, owner, acct):
    mom, mlink = env.member(owner, name="Mom")
    dad, dlink = env.member(owner, name="Dad")
    _, mom_ids = plant(env, mlink["user"]["id"], [("Mom's", "mom's own words")])
    _, dad_ids = plant(env, dlink["user"]["id"], [("Dad's", "dad's own words")])
    from tests.test_family import seed_owner_conversation
    seed_owner_conversation(env.owner_store)
    files = unzip(mom.get("/api/me/export"))
    blob = "\n".join(files.values())
    assert "mom's own words" in blob
    assert "dad's own words" not in blob and "Dad" not in blob
    assert "Kyle's private" not in blob
    assert not any(cid in blob for cid in dad_ids)
    # an id in the query changes nothing
    files = unzip(mom.get(f"/api/me/export?id={dlink['user']['id']}"))
    assert "dad's own words" not in "\n".join(files.values())


def test_export_is_for_family_members_only(env, owner, acct):
    assert owner.get("/api/me/export").status_code == 403
    assert env.client().get("/api/me/export").status_code == 401


def test_export_enforces_the_cap_and_cleans_up(env, owner, acct):
    mom, link = env.member(owner)
    plant(env, link["user"]["id"], [("Big", "x" * 5000)])
    env.config["family"]["export_max_mb"] = 0.002  # 2,000 bytes
    r = mom.get("/api/me/export")
    assert r.status_code == 413
    assert "larger than" in r.json()["error"]
    assert list(acct.tmp_root.iterdir()) == []  # nothing left behind
    assert not acct._busy                        # and the slot is free
    env.config["family"]["export_max_mb"] = 1
    assert mom.get("/api/me/export").status_code == 200


def test_export_removes_its_temp_files(env, owner, acct):
    mom, link = env.member(owner)
    plant(env, link["user"]["id"])
    made = []
    real = account_mod.tempfile.mkdtemp

    def spy(**kw):
        made.append(real(**kw))
        return made[-1]
    account_mod.tempfile.mkdtemp, saved = spy, real
    try:
        assert mom.get("/api/me/export").status_code == 200
    finally:
        account_mod.tempfile.mkdtemp = saved
    assert len(made) == 1 and made[0].startswith(str(acct.tmp_root))
    assert list(acct.tmp_root.iterdir()) == []
    assert not acct._busy


def test_a_dropped_download_is_swept_later(env, owner, acct):
    mom, link = env.member(owner)
    uid = link["user"]["id"]
    folder, path = acct.build(uid)  # built, never streamed
    import os
    old = env.clock.now - 3600
    os.utime(folder, (old, old))
    acct._busy.clear()
    assert mom.get("/api/me/export").status_code == 200
    assert not folder.exists()


def test_delete_removes_an_export_that_was_never_streamed(env, owner, acct):
    mom, link = env.member(owner)
    uid = link["user"]["id"]
    plant(env, uid)
    folder, path = acct.build(uid)  # built; the download never starts
    assert path.exists() and acct._busy.get(uid) is not None
    # and one an earlier run left (not in this run's map)
    older = acct.tmp_root / f"export-{uid}.left"
    older.mkdir()
    dad, dlink = env.member(owner, name="Dad")  # someone else's stays
    dfolder, _ = acct.build(dlink["user"]["id"])
    assert delete_me(mom).status_code == 200
    assert not folder.exists() and not older.exists()
    assert uid not in acct._busy
    assert dfolder.exists()
    acct.finish(dlink["user"]["id"], dfolder)


def test_a_client_that_leaves_before_the_body_still_cleans_up(env, owner, acct):
    """ASGI 2.4: the server's send raises OSError at the response start;
    Starlette turns it into ClientDisconnect and the generator never runs."""
    import anyio
    from starlette.requests import ClientDisconnect

    mom, link = env.member(owner)
    uid = link["user"]["id"]
    plant(env, uid)
    cookie = "; ".join(f"{k}={v}" for k, v in mom.cookies.items())
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
             "http_version": "1.1", "method": "GET", "scheme": "https",
             "path": "/api/me/export", "raw_path": b"/api/me/export",
             "query_string": b"", "root_path": "",
             "headers": [(b"host", b"testserver"), (b"cookie", cookie.encode())],
             "client": ("127.0.0.1", 5000), "server": ("testserver", 443)}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    started = []

    async def send(message):
        if message["type"] == "http.response.start":
            started.append(message["status"])
            raise OSError("the phone went away")

    async def run():
        with pytest.raises((ClientDisconnect, OSError)):
            await env.app(scope, receive, send)
    anyio.run(run)
    assert started == [200]
    assert list(acct.tmp_root.iterdir()) == []
    assert not acct._busy
    assert mom.get("/api/me/export").status_code == 200  # not stuck at 409


def test_a_client_that_leaves_mid_body_closes_the_zip_before_cleanup(
        env, owner, acct, monkeypatch):
    """The phone drops after the first chunk: the generator is suspended
    with the zip open. It must be closed before the folder is removed
    (Windows can't remove an open file), and the folder is gone as soon as
    the app returns, without waiting for gc."""
    import anyio
    from starlette.requests import ClientDisconnect

    mom, link = env.member(owner)
    uid = link["user"]["id"]
    plant(env, uid)
    opened, seen = [], []
    real_open = open

    def spy_open(*a, **kw):
        f = real_open(*a, **kw)
        opened.append(f)
        return f
    monkeypatch.setattr(account_mod, "open", spy_open, raising=False)
    real_finish = acct.finish

    def finish(u, folder):
        seen.append([f.closed for f in opened])  # at cleanup time
        real_finish(u, folder)
    monkeypatch.setattr(acct, "finish", finish)
    cookie = "; ".join(f"{k}={v}" for k, v in mom.cookies.items())
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
             "http_version": "1.1", "method": "GET", "scheme": "https",
             "path": "/api/me/export", "raw_path": b"/api/me/export",
             "query_string": b"", "root_path": "",
             "headers": [(b"host", b"testserver"), (b"cookie", cookie.encode())],
             "client": ("127.0.0.1", 5000), "server": ("testserver", 443)}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    bodies = []

    async def send(message):
        if message["type"] == "http.response.body":
            bodies.append((len(message["body"]), message["more_body"]))
            raise OSError("the phone went away")  # after the first chunk

    async def run():
        with pytest.raises((ClientDisconnect, OSError)):
            await env.app(scope, receive, send)
        # checked inside the loop, before the app's objects can be collected
        assert list(acct.tmp_root.iterdir()) == []
    gc_was = gc.isenabled()
    gc.disable()
    try:
        anyio.run(run)
    finally:
        if gc_was:
            gc.enable()
    (size, more), = bodies
    assert size > 0 and more  # really mid-body: the generator is suspended
    assert len(opened) == 1 and opened[0].closed
    assert seen and all(seen[0]), seen  # closed before the first cleanup
    assert not acct._busy


def test_startup_sweeps_old_export_folders(env, owner, acct):
    root = env.family.dir / ".exports"
    root.mkdir(parents=True, exist_ok=True)
    old, fresh = root / "export-u_gone12345.abc", root / "export-u_live12345.def"
    old.mkdir()
    (old / "hey-claude-data.zip").write_bytes(b"PK")
    fresh.mkdir()
    then = time.time() - 3600
    os.utime(old, (then, then))
    account_mod.Account(env.family)  # what register() makes at startup
    assert not old.exists()
    assert fresh.exists()  # maybe another process's download in progress


def test_exports_live_privately_in_the_family_dir(env, owner, acct):
    from kwil import family as family_mod

    assert not family_mod.valid_id(account_mod.EXPORTS_DIR)  # never someone's folder
    mom, link = env.member(owner)
    dad, dlink = env.member(owner, name="Dad")
    uid = link["user"]["id"]
    folder, _ = acct.build(uid)
    assert folder.parent == env.family.dir / ".exports"
    if os.name != "nt":
        assert (folder.parent.stat().st_mode & 0o777) == 0o700
        assert (folder.stat().st_mode & 0o777) == 0o700
    # removing someone else, and reloading the list, leave it alone
    assert env.family.remove(dlink["user"]["id"])
    assert folder.exists()
    again = family_mod.Family(env.config)
    assert set(again.users) == {uid} and folder.exists()
    acct.finish(uid, folder)
    assert not folder.exists() and not acct._busy


def test_one_export_at_a_time(env, owner, acct):
    mom, link = env.member(owner)
    uid = link["user"]["id"]
    plant(env, uid)
    folder, path = acct.build(uid)        # one being made / downloaded
    r = mom.get("/api/me/export")
    assert r.status_code == 409
    chunks = b"".join(acct.stream(uid, folder, path))  # that download finishes
    assert zipfile.ZipFile(io.BytesIO(chunks)).namelist()
    assert not folder.exists()
    assert mom.get("/api/me/export").status_code == 200
    # someone else isn't held up by it
    dad, _ = env.member(owner, name="Dad")
    folder2, path2 = acct.build(uid)
    assert dad.get("/api/me/export").status_code == 200
    list(acct.stream(uid, folder2, path2))


def test_an_old_export_ending_late_never_frees_a_newer_ones_slot(env, owner, acct):
    """Export 1's download runs past STALE, export 2 starts, and export 1
    ends while export 2 is still being built (before its folder exists) or
    after: either way export 2 keeps the slot and a third waits (409)."""
    mom, link = env.member(owner)
    uid = link["user"]["id"]
    plant(env, uid)
    f1, p1 = acct.build(uid)
    g1 = acct.stream(uid, f1, p1)
    next(g1)                                   # export 1's download is under way
    env.clock.now += account_mod.STALE + 1     # ... and has gone stale
    real_sweep = acct._sweep

    def sweep():                               # in export 2's build, before mkdtemp
        g1.close()                             # export 1 ends right then
        real_sweep()
    acct._sweep = sweep
    try:
        f2, p2 = acct.build(uid)
    finally:
        acct._sweep = real_sweep
    assert not f1.exists() and f2.exists()
    assert acct._busy.get(uid) is not None
    assert mom.get("/api/me/export").status_code == 409   # no third export
    acct.finish(uid, f1)                       # export 1's cleanup again, later
    assert mom.get("/api/me/export").status_code == 409
    assert b"".join(acct.stream(uid, f2, p2))  # export 2 ends: its slot frees
    assert not f2.exists() and not acct._busy
    assert mom.get("/api/me/export").status_code == 200



def test_a_delete_during_a_build_leaves_no_slot_behind(env, owner, acct):
    """The account is deleted while its export is being built (after the
    slot is taken, before the folder is bound to it): the build must not
    bring the slot back for an account that no longer exists."""
    mom, link = env.member(owner)
    uid = link["user"]["id"]
    plant(env, uid)
    real_sweep = acct._sweep

    def sweep():                               # in the build, before mkdtemp
        acct._busy.pop(uid, None)              # what delete does to the slot
        real_sweep()
    acct._sweep = sweep
    try:
        folder, path = acct.build(uid)
    finally:
        acct._sweep = real_sweep
    assert uid not in acct._busy
    acct.finish(uid, folder)


def test_a_build_failing_early_never_frees_a_newer_exports_slot(env, owner, acct, monkeypatch):
    """Export 1 stalls past STALE before its folder is made; export 2 takes
    the slot; export 1's mkdtemp then fails. Export 2 keeps its slot."""
    mom, link = env.member(owner)
    uid = link["user"]["id"]
    real_sweep = acct._sweep
    newer = {}

    def sweep():                               # export 1, before its mkdtemp
        env.clock.now += account_mod.STALE + 1
        acct._sweep = real_sweep
        newer["token"] = acct._start(uid)      # export 2 takes over the slot
    acct._sweep = sweep
    monkeypatch.setattr(account_mod.tempfile, "mkdtemp",
                        lambda **kw: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        acct.build(uid)
    slot = acct._busy.get(uid)
    assert slot is not None and slot[1] is newer["token"]

def test_export_is_rate_limited(env, owner, acct):
    mom, link = env.member(owner)
    for _ in range(account_mod.EXPORTS_PER_HOUR):
        assert mom.get("/api/me/export").status_code == 200
    r = mom.get("/api/me/export")
    assert r.status_code == 429
    dad, _ = env.member(owner, name="Dad")      # per person
    assert dad.get("/api/me/export").status_code == 200
    env.clock.now += 3601
    assert mom.get("/api/me/export").status_code == 200


def test_rate_limit_map_is_bounded(env, owner, acct):
    for i in range(50):
        acct._recent[f"u_gone{i:08d}"] = account_mod.deque([env.clock.now - 7200])
    mom, _ = env.member(owner)
    assert mom.get("/api/me/export").status_code == 200
    assert len(acct._recent) == 1


# --- logs ---------------------------------------------------------------------------

def test_logs_carry_ids_only(env, owner, acct, caplog):
    mom, link = env.member(owner, name="Grandma Wu")
    uid = link["user"]["id"]
    plant(env, uid, [("Secret plans", "my email is grandma@example.com")])
    caplog.set_level(logging.DEBUG)
    assert mom.get("/api/me/export").status_code == 200
    env.config["family"]["export_max_mb"] = 0.0001
    acct._recent.clear()
    assert mom.get("/api/me/export").status_code == 413
    assert delete_me(mom, confirm="Grandma Wu").status_code == 200
    text = caplog.text
    for leaked in ("Grandma", "grandma@example.com", "Secret plans", REFRESH, ACCESS,
                   *KEY_FILES.values()):
        assert leaked not in text, leaked
    info = [r.getMessage() for r in caplog.records
            if r.name == "kwil.account" and r.levelno >= logging.INFO]
    assert any(uid in m and "exported" in m for m in info)
    assert any(uid in m and "deleted their account" in m for m in info)
    assert any(uid in m and "revoked" in m for m in info)


# --- the public pages say so ------------------------------------------------------

def test_privacy_and_terms_pages_mention_self_service():
    """/privacy and /terms tell people they can do it themselves in Settings
    (true: family.js calls these routes), and keep the support-email route."""
    from pathlib import Path

    web = Path(account_mod.__file__).parent / "web"
    js = (web / "family.js").read_text(encoding="utf-8")
    assert '"/api/me/export"' in js and '"/api/me/delete"' in js
    for page in ("privacy.html", "terms.html"):
        text = " ".join((web / page).read_text(encoding="utf-8").split())
        assert "download your data and delete your account yourself in Settings" in text \
            or ("In Settings you can download your data" in text
                and "delete your account yourself" in text), page
    privacy = " ".join((web / "privacy.html").read_text(encoding="utf-8").split())
    assert "ask at {{SUPPORT_EMAIL}} to have your account" in privacy
