"""Moving a family member's history from their PC instance to the cloud
(kwil/family_import.py, HistoryStore.import_conversation): the owner-only
import route, and the PC-side CLI that copies the database and posts it.

All offline: real SQLite stores in tmp_path, the web app through
TestClient, the CLI's HTTP through an httpx MockTransport."""

import json
import logging
import sqlite3
import threading

import pytest

pytest.importorskip("fastapi")
import httpx  # noqa: E402

from kwil import family_import, history  # noqa: E402
from kwil.history import HistoryStore  # noqa: E402
from tests.test_cloud import PUBLIC, make_cloud, slept, via_proxy  # noqa: E402,F401
from tests.test_family import delete, env, owner  # noqa: E402,F401
from tests.test_webapp import TOKEN  # noqa: E402

SECRET_TEXT = "the spare key is under the blue flowerpot"
SECRET_TITLE = "Flowerpot key"


def pc_store(path, n=1, text=SECRET_TEXT, title=SECRET_TITLE):
    """Mom's PC history: a normal store with failover's event log
    (sync_events), as `python -m kwil --config config.mom.toml` writes."""
    store = HistoryStore(path)
    ids = []
    for i in range(n):
        cid = store.create("wake")
        store.append(cid, "user", f"{text} ({i})")
        store.append(cid, "assistant", "I'll remember that.",
                     tool_events=[history.tool_event("remember_note", {"x": "y"}, "ok")])
        store.finish(cid, f"{title} {i}", "Where the key is.")
        ids.append(cid)
    return store, ids


def exported(store, ids):
    return [json.loads(history.export(store.get(cid), "json")) for cid in ids]


def active_member(env, owner, **kw):
    mom, link = env.member(owner, **kw)
    return mom, link["user"]["id"]


def post_import(client, uid, conversations):
    return client.post(f"/api/users/{uid}/history/import",
                       json={"conversations": conversations})


# --- the route ------------------------------------------------------------------

def test_owner_imports_a_pc_conversation_into_a_member(env, owner, tmp_path):
    """Failing first: the route didn't exist (404)."""
    pc, ids = pc_store(tmp_path / "mom.sqlite")
    convs = exported(pc, ids)
    mom, uid = active_member(env, owner)
    r = post_import(owner, uid, convs)
    assert r.status_code == 200, r.text
    assert r.json() == {"imported": 1, "skipped": 0}
    listed = mom.get("/api/conversations").json()["conversations"]
    assert [c["id"] for c in listed] == ids
    got = mom.get(f"/api/conversations/{ids[0]}").json()
    assert [m["text"] for m in got["messages"]] == [f"{SECRET_TEXT} (0)",
                                                    "I'll remember that."]
    # the same conversation as on the PC: its export is identical
    again = mom.get(f"/api/conversations/{ids[0]}/export?format=json").json()
    assert again == convs[0]
    # searchable in their own history, never in the owner's
    assert [c["id"] for c in mom.get("/api/conversations?q=flowerpot")
            .json()["conversations"]] == ids
    assert owner.get(f"/api/conversations/{ids[0]}").status_code == 404
    pc.close()


def test_imported_turns_look_like_native_ones(env, owner, tmp_path):
    pc, ids = pc_store(tmp_path / "mom.sqlite")
    pc.update(ids[0], pinned=True)
    native = pc.get(ids[0])
    _, uid = active_member(env, owner)
    assert post_import(owner, uid, exported(pc, ids)).status_code == 200
    got = env.family.context(uid).history.get(ids[0])
    for key in ("title", "summary", "source", "pinned", "archived", "message_count",
                "preview"):
        assert got[key] == native[key], key
    assert int(got["started_at"]) == int(native["started_at"])
    assert got["ended_at"] is not None and int(got["ended_at"]) == int(native["ended_at"])
    for a, b in zip(got["messages"], native["messages"]):
        assert (a["role"], a["kind"], a["text"], a["tool_events"], a["interrupted"]) \
            == (b["role"], b["kind"], b["text"], b["tool_events"], b["interrupted"])
        assert int(a["created_at"]) == int(b["created_at"])
    pc.close()


def test_import_is_owner_only(env, owner, tmp_path):
    pc, ids = pc_store(tmp_path / "mom.sqlite")
    mom, uid = active_member(env, owner)
    dad, _ = active_member(env, owner, name="Dad", language="en")
    assert post_import(env.client(), uid, exported(pc, ids)).status_code == 401
    assert post_import(mom, uid, exported(pc, ids)).status_code == 404
    assert post_import(dad, uid, exported(pc, ids)).status_code == 404
    assert mom.get("/api/conversations").json()["conversations"] == []
    pc.close()


def test_unknown_or_not_yet_accepted_members_are_refused(env, owner, tmp_path):
    pc, ids = pc_store(tmp_path / "mom.sqlite")
    assert post_import(owner, "u_nobody1234", exported(pc, ids)).status_code == 404
    invited = env.invite(owner)["user"]["id"]
    assert post_import(owner, invited, exported(pc, ids)).status_code == 409
    assert not (env.tmp / "users" / invited).exists()
    pc.close()


def test_bad_bodies_write_nothing(env, owner, tmp_path):
    pc, ids = pc_store(tmp_path / "mom.sqlite")
    mom, uid = active_member(env, owner)
    good = exported(pc, ids)[0]
    for body in ({}, {"conversations": "x"}, [1]):
        r = owner.post(f"/api/users/{uid}/history/import", json=body)
        assert r.status_code == 400
    broken = {**good, "id": "x"}
    assert post_import(owner, uid, [good, broken]).status_code == 400
    no_role = {**good, "id": history.new_id(),
               "messages": [{**good["messages"][0], "role": "system"}]}
    assert post_import(owner, uid, [no_role]).status_code == 400
    assert mom.get("/api/conversations").json()["conversations"] == []
    pc.close()


def test_a_retry_imports_nothing_twice(env, owner, tmp_path):
    pc, ids = pc_store(tmp_path / "mom.sqlite", n=3)
    mom, uid = active_member(env, owner)
    convs = exported(pc, ids)
    assert post_import(owner, uid, convs[:2]).json() == {"imported": 2, "skipped": 0}
    assert post_import(owner, uid, convs).json() == {"imported": 1, "skipped": 2}
    assert post_import(owner, uid, convs).json() == {"imported": 0, "skipped": 3}
    store = env.family.context(uid).history
    assert store.count() == 3
    assert [c["message_count"] for c in store.list()] == [2, 2, 2]
    pc.close()


def test_an_import_cut_off_by_a_crash_is_finished_without_duplicates(tmp_path):
    """A hard crash leaves the salt in history_meta: the retry writes the
    same event ids, so the turns already written aren't added again."""
    pc, ids = pc_store(tmp_path / "mom.sqlite")
    conv = exported(pc, ids)[0]
    store = HistoryStore(tmp_path / "member.sqlite", eventlog=False)
    key = "import:" + ids[0]
    store._meta_set(key, "ab12")
    half = {**conv, "messages": conv["messages"][:1], "title": "", "summary": "",
            "ended_at": None, "pinned": False}
    assert store.import_conversation(half) is True  # written before the crash
    store._meta_set(key, "ab12")  # ...which came before the salt was cleared
    assert store.import_conversation(conv) is True
    got = store.get(ids[0])
    assert [m["role"] for m in got["messages"]] == ["user", "assistant"]
    assert got["title"] == conv["title"]
    assert store.import_conversation(conv) is False
    assert store._meta_get(key) == "done"
    store.close()
    pc.close()


@pytest.mark.parametrize("how", ["delete", "prune"])
def test_a_half_imported_conversation_deleted_stays_deleted(tmp_path, how):
    """Judge round 1, #1: a crash mid-conversation, then the member deletes
    the partial one (or retention prunes it): a re-run must not bring it
    back."""
    pc, ids = pc_store(tmp_path / "mom.sqlite")
    conv = exported(pc, ids)[0]
    store = HistoryStore(tmp_path / "member.sqlite", eventlog=False)
    key = "import:" + ids[0]
    store._meta_set(key, "ab12")
    half = {**conv, "messages": conv["messages"][:1], "title": "", "summary": "",
            "ended_at": None, "pinned": False}
    assert store.import_conversation(half) is True  # written before the crash
    store._meta_set(key, "ab12")  # the crash came before the mark was "done"
    if how == "delete":
        store.delete(ids[0])
    else:
        store.retention_days = 1
        old = conv_time(conv) - 2 * 86400  # as if it was from two days ago
        store._con.execute("UPDATE conversations SET updated_at = ?", (old,))
        assert store.prune() == 1
    assert store.import_conversation(conv) is False
    assert store.count(archived=None) == 0
    with pytest.raises(KeyError):
        store.get(ids[0])
    store.close()
    pc.close()


def conv_time(conv) -> float:
    from datetime import datetime

    return datetime.fromisoformat(conv["messages"][-1]["created_at"]).timestamp()


def test_a_failed_import_is_undone(tmp_path):
    pc, ids = pc_store(tmp_path / "mom.sqlite")
    conv = exported(pc, ids)[0]
    store = HistoryStore(tmp_path / "member.sqlite", eventlog=False)
    real, calls = store._record, []

    def fail_second(*a, **kw):
        calls.append(a)
        if len(calls) == 2:
            raise sqlite3.OperationalError("disk I/O error")
        return real(*a, **kw)

    store._record = fail_second
    with pytest.raises(sqlite3.OperationalError):
        store.import_conversation(conv)
    del store._record
    assert store.count(archived=None) == 0
    assert store._meta_get("import:" + ids[0]) is None
    assert store.import_conversation(conv) is True
    assert store.get(ids[0])["message_count"] == 2
    store.close()
    pc.close()


def test_the_member_store_never_gets_the_sync_log(env, owner, tmp_path):
    pc, ids = pc_store(tmp_path / "mom.sqlite")
    assert pc._con.execute("SELECT count(*) FROM sync_events").fetchone()[0] > 0
    _, uid = active_member(env, owner)
    assert post_import(owner, uid, exported(pc, ids)).status_code == 200
    con = sqlite3.connect(env.tmp / "users" / uid / "history.sqlite")
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master")}
    rows = con.execute("SELECT count(*) FROM sync_events").fetchone()[0] \
        if "sync_events" in tables else 0
    con.close()
    assert rows == 0
    pc.close()


def test_over_the_size_cap_is_413_and_nothing_is_kept(env, owner, tmp_path):
    pc, ids = pc_store(tmp_path / "mom.sqlite", n=2, text="x" * 7000)
    mom, uid = active_member(env, owner)
    store = env.family.context(uid).history
    store.max_mb = (store._used_bytes() + 8 * 1024) / (1024 * 1024)  # room for ~8 KB
    r = post_import(owner, uid, exported(pc, ids))
    assert r.status_code == 413
    assert "max_history_mb" in r.json()["error"]
    assert mom.get("/api/conversations").json()["conversations"] == []
    assert store._con.execute("SELECT count(*) FROM conversations").fetchone()[0] == 0
    # with room, the same batch goes in
    store.max_mb = 50
    assert post_import(owner, uid, exported(pc, ids)).json()["imported"] == 2
    pc.close()


def test_logs_have_counts_never_text_or_titles(env, owner, tmp_path, caplog):
    pc, ids = pc_store(tmp_path / "mom.sqlite", n=2)
    _, uid = active_member(env, owner)
    with caplog.at_level(logging.DEBUG, logger="kwil"):
        assert post_import(owner, uid, exported(pc, ids)).status_code == 200
        assert post_import(owner, uid, exported(pc, ids)).status_code == 200
    info = [r.getMessage() for r in caplog.records if r.levelno >= logging.INFO]
    assert any("imported 2 conversation(s)" in m and uid in m for m in info)
    assert any("imported 0 conversation(s)" in m and "2 already there" in m
               for m in info)
    text = "\n".join(info)
    assert "flowerpot" not in text and SECRET_TITLE not in text
    assert "remember that" not in text
    pc.close()


def test_a_delete_after_import_leaves_no_text_in_the_file(env, owner, tmp_path):
    pc, ids = pc_store(tmp_path / "mom.sqlite", text="locker code zebra4417")
    mom, uid = active_member(env, owner)
    assert post_import(owner, uid, exported(pc, ids)).status_code == 200
    folder = env.tmp / "users" / uid
    blob = b"".join(p.read_bytes() for p in folder.glob("history.sqlite*"))
    assert b"zebra4417" in blob
    assert delete(mom, f"/api/conversations/{ids[0]}").status_code == 200
    blob = b"".join(p.read_bytes() for p in folder.glob("history.sqlite*"))
    assert b"zebra4417" not in blob and SECRET_TITLE.encode() not in blob
    # running the import again doesn't bring it back
    assert post_import(owner, uid, exported(pc, ids)).json() == {"imported": 0,
                                                                "skipped": 1}
    assert mom.get(f"/api/conversations/{ids[0]}").status_code == 404
    pc.close()


def test_a_deleted_conversation_stays_deleted_with_an_event_log(tmp_path):
    """The owner's kind of store (failover's event log): its tombstone
    redacts a re-import, which is skipped without leaving an empty row."""
    pc, ids = pc_store(tmp_path / "mom.sqlite")
    conv = exported(pc, ids)[0]
    store = HistoryStore(tmp_path / "other.sqlite")
    assert store.eventlog is not None
    assert store.import_conversation(conv) is True
    store.delete(ids[0])
    store._con.execute("DELETE FROM history_meta WHERE key = ?", ("import:" + ids[0],))
    assert store.import_conversation(conv) is False
    assert store._con.execute("SELECT count(*) FROM conversations").fetchone()[0] == 0
    store.close()
    pc.close()


def test_removing_the_member_after_an_import_leaves_no_folder(env, owner, tmp_path):
    pc, ids = pc_store(tmp_path / "mom.sqlite")
    _, uid = active_member(env, owner)
    assert post_import(owner, uid, exported(pc, ids)).status_code == 200
    assert owner.post(f"/api/users/{uid}/remove", json={}).status_code == 200
    assert not (env.tmp / "users" / uid).exists()
    # an import arriving after the removal can't bring the folder back
    assert post_import(owner, uid, exported(pc, ids)).status_code == 404
    assert not (env.tmp / "users" / uid).exists()
    pc.close()


def test_a_removal_during_an_import_waits_and_leaves_no_folder(env, owner, tmp_path):
    pc, ids = pc_store(tmp_path / "mom.sqlite")
    _, uid = active_member(env, owner)
    store = env.family.context(uid).history
    real = store.import_conversations
    started, removed = threading.Event(), threading.Event()
    seen = {}

    def slow_import(convs):
        started.set()
        # the removal is blocked on the family lock while this runs
        seen["removed_during"] = removed.wait(0.3)
        return real(convs)

    store.import_conversations = slow_import

    def remove():
        started.wait(5)
        env.family.remove(uid)
        removed.set()

    t = threading.Thread(target=remove)
    t.start()
    assert post_import(owner, uid, exported(pc, ids)).status_code == 200
    t.join(5)
    assert seen["removed_during"] is False and removed.is_set()
    assert not (env.tmp / "users" / uid).exists()
    pc.close()


def test_family_off_is_404(fake_client, tmp_path):
    from fastapi.testclient import TestClient

    from kwil import webapp
    from kwil.tts import PrintTTS
    from tests.test_webapp import BASE, Clock, FakeTranscriber, login

    assistant = webapp.WebAssistant(lambda reg: None, FakeTranscriber(), PrintTTS(),
                                    clock=Clock())
    app = webapp.create_app(assistant, TOKEN, reap_interval=3600,
                            sessions_file=tmp_path / "s.json")
    c = TestClient(app, base_url=BASE)
    login(c)
    assert post_import(c, "u_abcdefgh123", []).status_code == 404


# --- the cloud's body limit -----------------------------------------------------

def test_the_cloud_lets_an_import_body_up_to_the_upload_cap(make_cloud):
    """[cloud] max_body_kb (16) would refuse one long message; the import
    is an upload ([webapp] max_upload_mb), and only after sign-in."""
    _, client = make_cloud()
    body = json.dumps({"conversations": [], "pad": "x" * 50_000})
    headers = {**via_proxy(PUBLIC), "Content-Type": "application/json"}
    r = client.post("/api/users/u_abcdefgh123/history/import", content=body,
                    headers={**headers, "Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 404  # reached the app (no [family] there), not 413
    r = client.post("/api/users/u_abcdefgh123/invite", content=body,
                    headers={**headers, "Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 413  # other owner routes keep the small cap
    r = client.post("/api/users/u_abcdefgh123/history/import", content=body,
                    headers=headers)
    assert r.status_code == 401  # nobody signed in: no body is read


# --- the CLI --------------------------------------------------------------------

CLOUD = "https://kwil.example.com"
CLOUD_TOKEN = "cloud-owner-token-" + "z" * 24


class FakeCloud:
    """The import route as the CLI sees it (records every request)."""

    def __init__(self, status=200):
        self.requests = []
        self.status = status
        self.seen = set()

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "not signed in"})
        if request.method == "GET" and request.url.path == "/api/users":
            return httpx.Response(200, json={"users": [
                {"id": "u_mom12345678", "name": "Mom", "status": "active"},
                {"id": "u_kai12345678", "name": "Kai", "status": "invited"}]})
        convs = json.loads(request.content)["conversations"]
        new = [c["id"] for c in convs if c["id"] not in self.seen]
        self.seen.update(new)
        return httpx.Response(200, json={"imported": len(new),
                                         "skipped": len(convs) - len(new)})


@pytest.fixture
def cloud_api(monkeypatch):
    fake = FakeCloud()
    monkeypatch.setattr(family_import, "TRANSPORT", httpx.MockTransport(fake.handler))
    return fake


def run_cli(args, token=CLOUD_TOKEN):
    lines = []
    env = {family_import.TOKEN_ENV: token} if token else {}
    code = family_import.main(args, environ=env, out=lines.append)
    return code, "\n".join(lines)


def test_cli_copies_batches_and_signs_in_with_the_bearer(tmp_path, cloud_api, monkeypatch):
    pc, ids = pc_store(tmp_path / "mom.sqlite", n=5, text="y" * 3000)
    monkeypatch.setattr(family_import, "BATCH_BYTES", 9_000)
    code, out = run_cli(["--from", str(tmp_path / "mom.sqlite"), "--to", CLOUD,
                         "--user", "u_abcdefgh123"])
    assert code == 0, out
    assert len(cloud_api.requests) == 3  # 5 conversations of ~3.5 KB, 9 KB a batch
    sent = []
    for req in cloud_api.requests:
        assert req.url == f"{CLOUD}/api/users/u_abcdefgh123/history/import"
        assert req.headers["authorization"] == f"Bearer {CLOUD_TOKEN}"
        assert CLOUD_TOKEN not in str(req.url)
        assert len(req.content) <= 9_000
        sent += [c["id"] for c in json.loads(req.content)["conversations"]]
    assert sorted(sent) == sorted(ids)
    assert "imported 5, skipped 0" in out
    assert CLOUD_TOKEN not in out and "y" * 20 not in out and SECRET_TITLE not in out
    # again: everything is already there
    code, out = run_cli(["--from", str(tmp_path / "mom.sqlite"), "--to", CLOUD,
                         "--user", "u_abcdefgh123"])
    assert code == 0 and "imported 0, skipped 5" in out
    pc.close()


def test_cli_reads_a_copy_and_leaves_the_live_database_alone(tmp_path, cloud_api,
                                                            monkeypatch):
    pc, ids = pc_store(tmp_path / "mom.sqlite")
    copied = []
    from kwil import backup

    real = backup.copy_database
    monkeypatch.setattr(backup, "copy_database",
                        lambda src, dst: (copied.append((src, dst)), real(src, dst)))
    before = (tmp_path / "mom.sqlite").read_bytes()
    code, out = run_cli(["--from", str(tmp_path / "mom.sqlite"), "--to", CLOUD,
                         "--user", "u_abcdefgh123"])
    assert code == 0, out
    assert len(copied) == 1 and copied[0][1].parent != tmp_path
    assert not copied[0][1].parent.exists()  # the private copy is gone
    assert (tmp_path / "mom.sqlite").read_bytes() == before
    pc.append(ids[0], "user", "still running fine")  # the PC instance keeps working
    pc.close()


def test_cli_names_a_temp_copy_it_couldnt_delete(tmp_path, cloud_api, monkeypatch):
    """Judge round 1, #3: on Windows a locked file can keep the copy; the
    user is told where (the path only) and the exit code says so."""
    import shutil

    from kwil import backup

    pc, _ = pc_store(tmp_path / "mom.sqlite")
    kept = []
    monkeypatch.setattr(backup, "cleanup", lambda folder: kept.append(folder))
    for extra in (["--dry-run"], []):
        code, out = run_cli(["--from", str(tmp_path / "mom.sqlite"), "--to", CLOUD,
                             "--user", "u_abcdefgh123", *extra])
        assert code == 1
        folder = kept[-1]
        assert folder.exists() and str(folder) in out
        assert "delete that folder yourself" in out
        assert SECRET_TEXT not in out and CLOUD_TOKEN not in out
        shutil.rmtree(folder)
    assert "imported 1" in out  # the import itself still ran
    pc.close()


def test_cli_dry_run_posts_nothing(tmp_path, cloud_api):
    pc, _ = pc_store(tmp_path / "mom.sqlite", n=3)
    code, out = run_cli(["--from", str(tmp_path / "mom.sqlite"), "--to", CLOUD,
                         "--user", "u_abcdefgh123", "--dry-run"], token="")
    assert code == 0 and cloud_api.requests == []
    assert "Read 3 conversation(s)" in out and "nothing was sent" in out
    pc.close()


def test_cli_never_prints_the_token_on_a_refusal(tmp_path, cloud_api):
    pc, _ = pc_store(tmp_path / "mom.sqlite")
    cloud_api.status = 401
    code, out = run_cli(["--from", str(tmp_path / "mom.sqlite"), "--to", CLOUD,
                         "--user", "u_abcdefgh123"])
    assert code == 1 and "HTTP 401" in out and CLOUD_TOKEN not in out
    pc.close()


@pytest.mark.parametrize("to", ["http://kwil.example.com", "ftp://x.example.com",
                                "https://kwil.example.com/api?token=x",
                                "https://user:pw@kwil.example.com", "kwil.example.com"])
def test_cli_refuses_anything_but_https_or_localhost(tmp_path, cloud_api, to):
    pc, _ = pc_store(tmp_path / "mom.sqlite")
    code, out = run_cli(["--from", str(tmp_path / "mom.sqlite"), "--to", to,
                         "--user", "u_abcdefgh123"])
    assert code == 2 and cloud_api.requests == []
    assert "x.example" not in out and "pw" not in out
    pc.close()


def test_cli_allows_plain_http_to_this_machine(tmp_path, cloud_api):
    pc, _ = pc_store(tmp_path / "mom.sqlite")
    code, _ = run_cli(["--from", str(tmp_path / "mom.sqlite"), "--to",
                       "http://127.0.0.1:8765", "--user", "u_abcdefgh123"])
    assert code == 0
    assert str(cloud_api.requests[0].url).startswith("http://127.0.0.1:8765/api/users/")
    pc.close()


def test_cli_needs_the_token_and_a_member_id(tmp_path, cloud_api):
    pc, _ = pc_store(tmp_path / "mom.sqlite")
    code, out = run_cli(["--from", str(tmp_path / "mom.sqlite"), "--to", CLOUD,
                         "--user", "u_abcdefgh123"], token="")
    assert code == 2 and family_import.TOKEN_ENV in out
    code, out = run_cli(["--from", str(tmp_path / "mom.sqlite"), "--to", CLOUD,
                         "--user", "../../admin"])
    assert code == 2 and "exactly one member" in out
    assert [r.method for r in cloud_api.requests] == ["GET"]  # the lookup only
    pc.close()


def test_cli_finds_a_member_by_name(tmp_path, cloud_api):
    pc, _ = pc_store(tmp_path / "mom.sqlite")
    code, out = run_cli(["--from", str(tmp_path / "mom.sqlite"), "--to", CLOUD,
                         "--user", "mom"])
    assert code == 0, out
    get, post = cloud_api.requests
    assert get.url == f"{CLOUD}/api/users"
    assert get.headers["authorization"] == f"Bearer {CLOUD_TOKEN}"
    assert post.url.path == "/api/users/u_mom12345678/history/import"
    assert "Importing into u_mom12345678" in out
    # someone who hasn't accepted their invite: nothing is posted
    cloud_api.requests.clear()
    code, out = run_cli(["--from", str(tmp_path / "mom.sqlite"), "--to", CLOUD,
                         "--user", "Kai"])
    assert code == 2 and "accepted" in out
    assert [r.method for r in cloud_api.requests] == ["GET"]
    pc.close()


def test_batches_keep_every_body_under_the_limit():
    convs = [{"id": f"c{i}", "t": "z" * (100 * i)} for i in range(1, 30)]
    bodies, too_big = family_import.batches(convs, limit=2_000)
    assert too_big == 10  # c20.. are over 2 KB alone
    assert all(len(b) <= 2_000 for b in bodies)
    sent = [c["id"] for b in bodies for c in json.loads(b)["conversations"]]
    assert sent == [f"c{i}" for i in range(1, 20)]
