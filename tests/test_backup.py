"""The owner's backup download (assistant/backup.py, GET /api/backup): a
consistent copy of every history database, the family list and the
waitlist; never a token, key or .enc file; size-capped; owner only; its
temp files removed. All offline: real SQLite in tmp_path."""

import io
import json
import logging
import os
import sqlite3
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from assistant import backup as backup_mod, family as family_mod, webapp  # noqa: E402
from assistant.chat import ClaudeChat  # noqa: E402
from assistant.config import DEFAULTS, load_config  # noqa: E402
from assistant.history import HistoryStore  # noqa: E402
from assistant.tts import PrintTTS  # noqa: E402
from tests.test_cloud import make_cloud, slept  # noqa: E402,F401 (fixtures)
from tests.test_family import Env  # noqa: E402
from tests.test_webapp import BASE, TOKEN, Clock, FakeTranscriber, login  # noqa: E402

SECRET_TOKEN = "ya29.a0-backup-test-google-access-token"
KEY_CIPHERTEXT = b"gAAAA-encrypted-anthropic-key"


@pytest.fixture
def env(fake_client, tmp_path):
    config = load_config(None, None)
    config["family"].update(enabled=True, dir=str(tmp_path / "data" / "users"))
    fam = family_mod.Family(config, client=fake_client)
    owner_store = HistoryStore(tmp_path / "data" / "history.sqlite", eventlog=False)
    cid = owner_store.create("web")
    owner_store.append(cid, "user", "the owner's question")
    owner_store.append(cid, "assistant", "the owner's answer")
    assistant = webapp.WebAssistant(
        lambda reg: ClaudeChat(client=fake_client, model="m", tools=reg),
        FakeTranscriber(), PrintTTS(), clock=Clock(), history=owner_store, family=fam)
    temp = tmp_path / "tmp"
    assistant.backup = backup_mod.Backup(
        history_path=owner_store.path, family=fam,
        waitlist_path=tmp_path / "data" / "waitlist.json", temp_dir=temp,
        max_bytes=DEFAULTS["webapp"]["backup_max_mb"] * 1_000_000)
    app = webapp.create_app(assistant, TOKEN, reap_interval=3600,
                            limiter=webapp.LoginLimiter(per_ip=3, global_delay=0))
    e = Env(app=app, assistant=assistant, family=fam, owner_store=owner_store,
            tmp=tmp_path, temp=temp, config=config)
    yield e
    fam.close()
    owner_store.close()


@pytest.fixture
def owner(env):
    c = TestClient(env.app, base_url=BASE)
    login(c)
    return c


def add_member(env, owner):
    """A family member with a history of their own, a Google token and an
    encrypted key in their folder."""
    r = owner.post("/api/invites", json={"name": "Mom", "language": "zh"})
    assert r.status_code == 200, r.text
    member = TestClient(env.app, base_url=BASE)
    invite = r.json()["url"].split("#invite=", 1)[1]
    assert member.post("/api/invite/accept", json={"invite": invite}).status_code == 200
    uid = next(iter(env.family.users))
    folder = env.family.dir / uid
    store = HistoryStore(folder / "history.sqlite", eventlog=False)
    cid = store.create("web")
    store.append(cid, "user", "mom's question")
    store.close()
    (folder / family_mod.GOOGLE_TOKEN).write_text(json.dumps({"token": SECRET_TOKEN}))
    (folder / "anthropic_key.enc").write_bytes(KEY_CIPHERTEXT)
    (folder / "notion.enc").write_bytes(KEY_CIPHERTEXT)
    return member, uid


def leftovers(env) -> list:
    """What is left in the backup's temp folder."""
    return list(env.temp.iterdir()) if env.temp.exists() else []


def open_zip(r) -> zipfile.ZipFile:
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    assert r.headers["cache-control"] == "no-store"
    return zipfile.ZipFile(io.BytesIO(r.content))


def rows(zf, name, tmp_path) -> list[str]:
    path = tmp_path / ("restored-" + name.replace("/", "-"))
    path.write_bytes(zf.read(name))
    con = sqlite3.connect(path)
    try:
        return [t for (t,) in con.execute("SELECT text FROM messages ORDER BY id")]
    finally:
        con.close()


def test_backup_has_every_database_and_no_secret(env, owner, tmp_path):
    _, uid = add_member(env, owner)
    (tmp_path / "data" / "waitlist.json").write_text('{"entries": []}')
    # a secret elsewhere on the disk must never be picked up either
    (tmp_path / "data" / "web_sessions.json").write_text(SECRET_TOKEN)
    zf = open_zip(owner.get("/api/backup"))
    names = set(zf.namelist())
    assert names == {"history.sqlite", f"users/{uid}/history.sqlite",
                     "users/users.json", "waitlist.json", "MANIFEST.json"}
    assert rows(zf, "history.sqlite", tmp_path) == ["the owner's question",
                                                    "the owner's answer"]
    assert rows(zf, f"users/{uid}/history.sqlite", tmp_path) == ["mom's question"]
    assert not any(n.endswith(".enc") or "token" in n for n in names)
    for name in names:
        data = zf.read(name)
        assert SECRET_TOKEN.encode() not in data and KEY_CIPHERTEXT not in data
    users = json.loads(zf.read("users/users.json"))["users"]
    assert users[uid]["name"] == "Mom" and "invite_hash" not in users[uid]
    assert users[uid] == family_mod.public(env.family.users[uid])  # tier included
    manifest = json.loads(zf.read("MANIFEST.json"))
    assert {f["name"] for f in manifest["files"]} == names - {"MANIFEST.json"}


def test_family_entries_are_built_from_an_allowlist(env, owner):
    """A field users.json gains later (a key, a token, a note) stays out of
    the backup until family.public lists it."""
    _, uid = add_member(env, owner)
    with env.family.lock:
        env.family.users = {uid: {**env.family.users[uid], "invite_hash": "h" * 64,
                                  "api_key_hint": SECRET_TOKEN, "future_field": 1}}
    users = json.loads(open_zip(owner.get("/api/backup")).read("users/users.json"))["users"]
    assert set(users[uid]) == set(family_mod.public(env.family.users[uid]))
    assert "future_field" not in users[uid] and SECRET_TOKEN not in json.dumps(users)


def test_a_database_copy_is_one_consistent_file(env, owner, tmp_path):
    """sqlite3 backup: rows still only in the live database's WAL are in
    the copy, and the copy needs no -wal file of its own."""
    con = sqlite3.connect(env.owner_store.path)
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    con.close()
    zf = open_zip(owner.get("/api/backup"))
    path = tmp_path / "copy.sqlite"
    path.write_bytes(zf.read("history.sqlite"))
    con = sqlite3.connect(path)
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    con.close()


def test_a_family_member_and_strangers_get_nothing(env, owner):
    member, _ = add_member(env, owner)
    r = member.get("/api/backup")
    assert r.status_code == 404 and r.headers["content-type"] != "application/zip"
    assert TestClient(env.app, base_url=BASE).get("/api/backup").status_code == 401
    assert leftovers(env) == []


def test_the_size_cap_holds(env, owner, caplog, monkeypatch):
    env.assistant.backup.max_bytes = 1000  # smaller than the owner's history

    def never(src, dst):
        raise AssertionError("refused before copying anything")
    monkeypatch.setattr(backup_mod, "copy_database", never)
    with caplog.at_level(logging.INFO, logger="assistant.backup"):
        r = owner.get("/api/backup")
    monkeypatch.undo()
    assert r.status_code == 413 and "backup_max_mb" in r.json()["error"]
    assert leftovers(env) == []
    # a zip that only grows past the cap while being written is refused too
    b = env.assistant.backup
    b.max_bytes = backup_mod.Backup._size(Path(env.owner_store.path))
    b._size = lambda path: 0
    big = b.waitlist_path
    big.write_bytes(os.urandom(b.max_bytes))  # incompressible
    assert owner.get("/api/backup").status_code == 413
    assert leftovers(env) == []


def test_temp_files_are_removed_and_size_only_is_logged(env, owner, caplog):
    with caplog.at_level(logging.DEBUG, logger="assistant.backup"):
        zf = open_zip(owner.get("/api/backup"))
    assert "history.sqlite" in zf.namelist()
    assert env.temp.exists() and list(env.temp.iterdir()) == []
    lines = [r.getMessage() for r in caplog.records if r.levelno >= logging.INFO]
    assert len(lines) == 1 and lines[0].startswith("Backup downloaded (")
    assert "question" not in caplog.text and str(env.tmp) not in caplog.text


def test_each_database_copy_is_deleted_once_zipped(env, owner):
    """At most one database copy on the temp disk at a time: the folder
    holds only the zip when it is sent."""
    add_member(env, owner)
    path, folder = env.assistant.backup.build()
    try:
        assert list(folder.iterdir()) == [path]
        assert len(zipfile.ZipFile(path).namelist()) == 4
    finally:
        backup_mod.cleanup(folder)
    assert not folder.exists()


def test_a_crashed_backups_leftovers_are_swept(env, owner):
    env.temp.mkdir()
    old = env.temp / (backup_mod.TEMP_PREFIX + "crashed")
    old.mkdir()
    (old / "db-0.sqlite").write_text("a copy left behind")
    stale = time.time() - backup_mod.STALE_SECONDS - 60
    os.utime(old, (stale, stale))
    fresh = env.temp / (backup_mod.TEMP_PREFIX + "running")
    fresh.mkdir()  # another download in progress right now: kept
    open_zip(owner.get("/api/backup"))
    assert not old.exists() and fresh.exists()


def test_one_backup_at_a_time(env, owner):
    assert env.assistant.backup._busy.acquire(blocking=False)
    try:
        assert owner.get("/api/backup").status_code == 409
    finally:
        env.assistant.backup._busy.release()


def test_build_assistant_sets_up_the_backup_from_config(tmp_path):
    cfg = load_config(None, None)
    cfg["family"]["dir"] = str(tmp_path / "users")
    cfg["webapp"]["backup_max_mb"] = 7
    store = SimpleNamespace(path=str(tmp_path / "history.sqlite"))
    b = backup_mod.Backup.from_config(cfg, store, None)
    assert b.max_bytes == 7_000_000 and b.waitlist_path == tmp_path / "waitlist.json"
    assert str(b.history_path) == store.path
    assert backup_mod.Backup.from_config(cfg, None, None).history_path is None
    assert DEFAULTS["webapp"]["backup_max_mb"] == 200
    # [webapp] waitlist_file (the public site's setting) when there is one;
    # "" = no waitlist in the backup
    cfg["webapp"]["waitlist_file"] = str(tmp_path / "public" / "list.json")
    assert backup_mod.Backup.from_config(cfg, store, None).waitlist_path == \
        tmp_path / "public" / "list.json"
    cfg["webapp"]["waitlist_file"] = ""
    assert backup_mod.Backup.from_config(cfg, store, None).waitlist_path is None


def test_an_empty_waitlist_setting_leaves_it_out(env, owner, tmp_path):
    (tmp_path / "data" / "waitlist.json").write_text('{"entries": ["a@example.com"]}')
    cfg = {**env.config, "webapp": {**env.config["webapp"], "waitlist_file": ""}}
    env.assistant.backup.waitlist_path = backup_mod.Backup.from_config(
        cfg, env.owner_store, env.family).waitlist_path
    assert "waitlist.json" not in open_zip(owner.get("/api/backup")).namelist()


def test_startup_sweeps_a_crashed_backups_plaintext(tmp_path, monkeypatch):
    """build_assistant removes what a crash left in the temp folder, before
    anyone asks for a backup."""
    import tempfile

    from assistant import webapp as webapp_mod

    temp = tmp_path / "tmp"
    old = temp / (backup_mod.TEMP_PREFIX + "crashed")
    old.mkdir(parents=True)
    (old / "db-0.sqlite").write_text("the owner's conversations")
    stale = time.time() - backup_mod.STALE_SECONDS - 60
    os.utime(old, (stale, stale))
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    for var in ("PINECONE_API_KEY", "PINECONE_INDEX", "NOTION_TOKEN", "GOOGLE_TOKEN_JSON",
                "GOOGLE_CLIENT_JSON", "HISTORY_PATH", "HA_URL", "HA_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-a-key")
    monkeypatch.chdir(tmp_path)  # no credentials.json or token.json here
    cfg = load_config(None, None)
    cfg["history"]["path"] = str(tmp_path / "history.sqlite")
    cfg["family"]["dir"] = str(tmp_path / "users")
    assistant = webapp_mod.build_assistant(cfg, "print", lambda m: None)
    try:
        assert not old.exists()
        assert assistant.backup.temp_dir == temp
    finally:
        assistant.history.close()


def test_backup_downloads_through_the_cloud_guard(env, make_cloud):
    """On Render: the owner signed in through the TLS proxy gets the zip;
    nobody else gets past CloudGuard."""
    from tests.test_cloud import PUBLIC, login as cloud_login, via_proxy

    _, client = make_cloud(app=env.app)
    assert client.get("/api/backup", headers=via_proxy(PUBLIC)).status_code == 401
    assert cloud_login(client).status_code == 200
    zf = open_zip(client.get("/api/backup", headers=via_proxy(PUBLIC)))
    assert "history.sqlite" in zf.namelist()
    assert leftovers(env) == []
