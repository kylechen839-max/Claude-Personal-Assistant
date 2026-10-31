"""Failover on the cloud host: CloudGuard -> EdgeMiddleware -> web app,
built by cloud.build_app with [failover] role = "cloud"."""

import copy
import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from kwil import cloud, webapp  # noqa: E402
from kwil.chat import ClaudeChat  # noqa: E402
from kwil.config import DEFAULTS  # noqa: E402
from kwil.failover.sync import new_event  # noqa: E402
from kwil.failover.tokens import TokenAuthority  # noqa: E402
from kwil.history import HistoryStore  # noqa: E402
from kwil.tts import PrintTTS  # noqa: E402
from tests.test_cloud import PROXY, PUBLIC, at, via_proxy  # noqa: E402
from tests.test_webapp import TOKEN, FakeTranscriber  # noqa: E402

APP = "https://testserver"


@pytest.fixture
def cloud_app(fake_client, tmp_path):
    def make(role="cloud"):
        config = copy.deepcopy(DEFAULTS)
        config["cloud"]["trusted_proxies"] = ["10.0.0.0/8"]
        config["webapp"]["sessions_file"] = str(tmp_path / "sessions.json")
        config["failover"].update(enabled=True, role=role, app_origin=APP, backends=[
            {"role": "pi", "url": "https://pi.tail1234.ts.net"},
            {"role": "pc", "url": "https://pc.tail1234.ts.net"}])
        history = HistoryStore(tmp_path / "history.sqlite", origin="cloud")
        assistant = webapp.WebAssistant(
            lambda reg: ClaudeChat(client=fake_client, model="m", tools=reg),
            FakeTranscriber("hi"), PrintTTS(), history=history)
        guard = cloud.build_app(config, assistant, TOKEN)
        client = TestClient(at(PROXY, guard), base_url=APP, follow_redirects=False)
        return guard, client, history
    return make


def hdr(ip=PUBLIC, **extra):
    return via_proxy(ip, **extra)


def sign_in(client, ip=PUBLIC):
    r = client.post("/api/auth/token", json={"token": TOKEN}, headers=hdr(ip))
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def bearer(token, ip=PUBLIC):
    return hdr(ip, Authorization=f"Bearer {token}")


def test_build_app_starts_with_failover_and_healthz_reports_role(cloud_app):
    guard, client, _ = cloud_app()
    assert guard.web is not None  # CloudGuard sees the web app through EdgeMiddleware
    body = client.get("/healthz").json()
    assert body["ok"] is True and body["api"] == 1 and body["role"] == "cloud"
    assert set(body) == {"ok", "version", "api", "role"}


def test_router_module_is_public(cloud_app):
    _, client, _ = cloud_app()
    assert client.get("/backend-router.js", headers=hdr()).status_code == 200


def test_access_token_works_through_the_guard(cloud_app, fake_client):
    _, client, _ = cloud_app()
    token = sign_in(client)
    assert TOKEN not in token
    r = client.get("/api/state", headers=bearer(token))
    assert r.status_code == 200, r.text


def test_cookie_sign_in_still_works(cloud_app):
    _, client, _ = cloud_app()
    assert client.post("/api/login", json={"token": TOKEN}, headers=hdr()).status_code == 200
    assert client.get("/api/state", headers=hdr()).status_code == 200


def test_wrong_web_token_on_the_token_endpoint_counts(cloud_app):
    _, client, _ = cloud_app()
    for _ in range(5):
        r = client.post("/api/auth/token", json={"token": "x" * 43}, headers=hdr())
        assert r.status_code == 401
    assert client.post("/api/auth/token", json={"token": "x" * 43},
                       headers=hdr()).status_code == 429
    sign_in(client)  # the right one is never locked out


def test_bad_access_tokens_are_401_and_never_lock_anyone_out(cloud_app):
    _, client, _ = cloud_app()
    stale, _ = TokenAuthority(TOKEN).mint("app", ttl=-10_000)
    for _ in range(20):
        assert client.get("/api/state", headers=bearer(stale)).status_code == 401
        assert client.get("/api/state", headers=bearer("hc1.app.x.1.1.bad")).status_code == 401
    assert client.post("/api/auth/token", json={"token": "x" * 43},
                       headers=hdr()).status_code == 401  # not 429
    sign_in(client)


def test_sync_endpoints_need_a_sync_token_and_reach_history(cloud_app):
    _, client, history = cloud_app()
    app_token = sign_in(client)
    sync_token, _ = TokenAuthority(TOKEN).mint("sync", sid="pi")
    ev = new_event("message", "c" * 22, {"role": "user", "text": "from the pi"}, origin="pi")
    body = {"events": [ev.to_json()]}
    assert client.post("/api/sync/events", json=body,
                       headers=bearer(app_token)).status_code == 401
    r = client.post("/api/sync/events", json=body, headers=bearer(sync_token))
    assert r.status_code == 200 and r.json()["accepted"] == 1
    r = client.get("/api/sync/events?after=0", headers=bearer(sync_token))
    assert ev.id in [e["id"] for e in r.json()["events"]]
    # the pushed event shows up in the cloud's own history at once
    assert "from the pi" in json.dumps(history.sync_log.by_conv("c" * 22)[0].data)


def test_voice_socket_first_message_auth_through_the_guard(cloud_app):
    _, client, _ = cloud_app()
    token = sign_in(client)
    with client.websocket_connect("/api/voice", headers=hdr(Origin=APP)) as ws:
        ws.send_text(json.dumps({"type": "auth", "token": token}))
        assert json.loads(ws.receive_text()) == {"type": "auth_ok"}


def test_voice_socket_without_a_valid_token_is_closed(cloud_app):
    _, client, _ = cloud_app()
    with client.websocket_connect("/api/voice", headers=hdr(Origin=APP)) as ws:
        ws.send_text(json.dumps({"type": "auth", "token": "hc1.nope"}))
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_text()
    assert e.value.code == 4401


def test_voice_socket_from_another_site_is_still_refused(cloud_app):
    _, client, _ = cloud_app()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/voice",
                                      headers=hdr(Origin="https://evil.example")):
            pass


def test_without_a_role_the_guard_is_unchanged(cloud_app):
    guard, client, _ = cloud_app(role="")
    assert set(client.get("/healthz").json()) == {"ok", "version"}
    assert client.post("/api/auth/token", json={"token": TOKEN},
                       headers=hdr()).status_code == 401  # not public
    assert guard.failover_role is None


def test_pi_history_syncs_to_the_cloud_over_https(cloud_app, tmp_path):
    """A real Pi HistoryStore pushes through CloudGuard and the failover
    layer into the cloud's HistoryStore, and pulls the cloud's back."""
    from kwil.failover.peers import Backend
    from kwil.failover.sync import Syncer

    _, client, cloud_history = cloud_app()
    pi = HistoryStore(tmp_path / "pi.sqlite", origin="pi")
    cid = pi.create("wake")
    pi.append(cid, "user", "What's on my calendar tomorrow?")
    cloud_cid = cloud_history.create("web")
    cloud_history.append(cloud_cid, "user", "asked on the cloud")

    def transport(method, url, headers, body=None):
        path = url.split("testserver", 1)[1]
        r = client.request(method, path, json=body, headers={**hdr(), **headers})
        return r.status_code, r.json()

    syncer = Syncer(pi.sync_log, [Backend("cloud", APP)], TokenAuthority(TOKEN), "pi",
                    transport=transport)
    assert syncer.sync_all()["cloud"].startswith("ok")
    texts = [m["text"] for m in cloud_history.get(cid)["messages"]]
    assert texts == ["What's on my calendar tomorrow?"]
    assert [m["text"] for m in pi.get(cloud_cid)["messages"]] == ["asked on the cloud"]


def test_history_sync_thread_starts_on_the_pi_not_the_cloud(tmp_path, monkeypatch):
    import threading

    from kwil.failover import sync
    from kwil.failover.peers import settings_from_config

    ran = threading.Event()
    monkeypatch.setattr(sync.Syncer, "run_forever",
                        lambda self, stop, interval: ran.set())  # no network in tests

    config = copy.deepcopy(DEFAULTS)
    config["failover"].update(enabled=True, role="pi", app_origin=APP, sync_interval=3600,
                              backends=[{"role": "pc", "url": "https://pc.t.ts.net"}])
    assistant = webapp.WebAssistant(lambda reg: None, None, PrintTTS(),
                                    history=HistoryStore(tmp_path / "h.sqlite", origin="pi"))
    thread = webapp.start_history_sync(config, assistant, TOKEN, settings_from_config(config))
    assert thread is not None and thread.daemon and ran.wait(2)
    config["failover"]["role"] = "cloud"
    assert webapp.start_history_sync(config, assistant, TOKEN,
                                     settings_from_config(config)) is None


def test_unauthenticated_voice_sockets_are_capped_until_their_first_message(cloud_app):
    """A cookieless socket may open before its first-message token, but only
    a few at a time per address: they can't pile up waiting for the timeout."""
    guard, client, _ = cloud_app()
    token = sign_in(client)
    with client.websocket_connect("/api/voice", headers=hdr(Origin=APP)) as a, \
            client.websocket_connect("/api/voice", headers=hdr(Origin=APP)):
        assert guard.preauth_slots.count == 2  # both waiting for a token
        with pytest.raises(WebSocketDisconnect) as e:
            with client.websocket_connect("/api/voice", headers=hdr(Origin=APP)):
                pass
        assert e.value.code == 1008  # a third from the same address: refused
        a.send_text(json.dumps({"type": "auth", "token": token}))
        assert json.loads(a.receive_text()) == {"type": "auth_ok"}
        assert guard.preauth_slots.count == 1  # the first message freed its slot
    assert guard.preauth_slots.count == 0
    # another address is not affected by this one's waiting sockets
    with client.websocket_connect("/api/voice", headers=hdr("198.51.100.30",
                                                            Origin=APP)) as ws:
        ws.send_text(json.dumps({"type": "auth", "token": token}))
        assert json.loads(ws.receive_text()) == {"type": "auth_ok"}


# --- final review: sign out everywhere, voice over tokens, cookies on the cloud ---

def test_sign_out_everywhere_cuts_off_access_tokens_on_every_backend(cloud_app, tmp_path):
    from kwil.failover.sync import REVOKE_CONV

    _, client, history = cloud_app()
    token = sign_in(client)
    assert client.get("/api/state", headers=bearer(token)).status_code == 200
    assert client.post("/api/logout-all", headers=bearer(token)).status_code == 200
    assert client.get("/api/state", headers=bearer(token)).status_code == 401
    assert client.post("/api/auth/refresh", headers=bearer(token)).status_code == 401
    # it is in the synced log, so another backend that syncs refuses it too
    revoke = history.sync_log.by_conv(REVOKE_CONV)
    assert revoke
    pi_history = HistoryStore(tmp_path / "pi.sqlite", origin="pi")
    pi_history.sync_log.append(revoke)
    authority = TokenAuthority(TOKEN)
    from kwil.failover.edge import EdgeMiddleware
    from kwil.failover.sync import revoked_before

    edge = EdgeMiddleware(lambda *a: None, authority=authority, role="pi",
                          not_before=lambda: revoked_before(pi_history.sync_log))
    assert edge.app_claims(authority.verify(token)) is None
    # signing in again afterwards works
    import time

    time.sleep(1.05)  # sign-in times are in whole seconds
    fresh = sign_in(client)
    assert client.get("/api/state", headers=bearer(fresh)).status_code == 200


def test_voice_over_an_access_token_reaches_ready(cloud_app):
    _, client, _ = cloud_app()
    token = sign_in(client)
    with client.websocket_connect("/api/voice?auth=first", headers=hdr(Origin=APP)) as ws:
        ws.send_text(json.dumps({"type": "auth", "token": token}))
        assert json.loads(ws.receive_text()) == {"type": "auth_ok"}
        assert json.loads(ws.receive_text())["type"] == "ready"


def test_cookie_clients_on_the_cloud_with_failover(cloud_app):
    _, client, _ = cloud_app()
    same = hdr(Origin=APP)  # the app origin is the cloud's own origin
    assert client.post("/api/login", json={"token": TOKEN}, headers=same).status_code == 200
    r = client.get("/api/state", headers=same)
    assert r.status_code == 200 and "access-control-allow-origin" not in r.headers


def test_web_token_as_sync_bearer_is_not_counted(cloud_app):
    """WEB_TOKEN itself is right, just not a sync token: 401, not a failure."""
    _, client, _ = cloud_app()
    for _ in range(8):
        assert client.get("/api/sync/events", headers=bearer(TOKEN)).status_code == 401
    assert client.post("/api/auth/token", json={"token": "x" * 43},
                       headers=hdr()).status_code == 401  # not locked out


# --- cloud-only (2026-10-31): [failover] enabled = false on the server ---

def test_server_profile_turns_failover_off_even_with_the_old_env(
        fake_client, tmp_path, monkeypatch, caplog):
    """Render still has ASSISTANT_ROLE=cloud (and the backend list) until
    Kyle deletes them: the server profile's [failover] enabled = false wins,
    so the cloud is a plain cookie app: no role on /healthz, no public
    router module, no failover access tokens and no /api/sync."""
    import logging

    from kwil.config import load_config
    from kwil.failover import peers

    monkeypatch.setattr(peers, "_warned_disabled", False, raising=False)
    monkeypatch.setenv("ASSISTANT_PROFILE", "server")
    monkeypatch.setenv("ASSISTANT_ROLE", "cloud")
    monkeypatch.setenv("APP_ORIGIN", "https://app-origin-secret.example")
    monkeypatch.setenv("FAILOVER_BACKENDS", "pi=https://pi-secret.tail9.ts.net")
    config = load_config(None, None)
    assert config["failover"]["enabled"] is False
    config["webapp"]["sessions_file"] = str(tmp_path / "sessions.json")
    history = HistoryStore(tmp_path / "history.sqlite", origin="cloud")
    assistant = webapp.WebAssistant(
        lambda reg: ClaudeChat(client=fake_client, model="m", tools=reg),
        FakeTranscriber("hi"), PrintTTS(), history=history)
    with caplog.at_level(logging.DEBUG, logger="kwil"):
        guard = cloud.build_app(config, assistant, TOKEN)
    client = TestClient(at(PROXY, guard), base_url=APP, follow_redirects=False)

    assert guard.failover_role is None and guard.authority is None
    assert set(client.get("/healthz").json()) == {"ok", "version"}  # no "role"
    assert client.get("/backend-router.js", headers=hdr()).status_code == 401
    app_token, _ = TokenAuthority(TOKEN).mint("app")
    assert client.get("/api/state", headers=bearer(app_token)).status_code == 401
    sync_token, _ = TokenAuthority(TOKEN).mint("sync", sid="pi")
    assert client.get("/api/sync/events?after=0",
                      headers=bearer(sync_token)).status_code == 401
    assert client.post("/api/auth/token", json={"token": TOKEN},
                       headers=hdr()).status_code == 401  # no token sign-in
    # a signed-in browser finds no sync routes at all
    assert client.post("/api/login", json={"token": TOKEN}, headers=hdr()).status_code == 200
    assert client.get("/api/sync/events?after=0", headers=hdr()).status_code == 404
    assert client.get("/api/state", headers=hdr()).status_code == 200  # cookies work

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING
                and "ASSISTANT_ROLE" in r.getMessage()]
    assert len(warnings) == 1, [r.getMessage() for r in caplog.records]
    text = warnings[0].getMessage()
    assert "FAILOVER_BACKENDS" in text and "ignored" in text
    logged = " ".join(r.getMessage() for r in caplog.records)
    for value in ("pi-secret", "app-origin-secret", "tail9", "pi=https"):
        assert value not in logged


def test_failover_off_warns_once_and_only_when_something_is_set(monkeypatch, caplog):
    import logging

    from kwil.failover import peers

    monkeypatch.setattr(peers, "_warned_disabled", False, raising=False)
    monkeypatch.delenv("FAILOVER_BACKENDS", raising=False)
    config = copy.deepcopy(DEFAULTS)
    config["failover"]["enabled"] = False
    with caplog.at_level(logging.WARNING, logger="kwil"):
        assert peers.settings_from_config(config) == peers.FailoverSettings("", None, ())
    assert not caplog.records  # nothing left over: nothing to say
    config["failover"].update(role="cloud", app_origin=APP,
                              backends=[{"role": "pi", "url": "https://pi.t.ts.net"}])
    with caplog.at_level(logging.WARNING, logger="kwil"):
        for _ in range(3):  # many modules read [failover]
            s = peers.settings_from_config(config)
            assert (s.role, s.app_origin, s.backends) == ("", None, ())
    assert len(caplog.records) == 1
    assert "pi.t.ts.net" not in caplog.records[0].getMessage()
    config["failover"]["role"] = "nonsense"  # not even validated while off
    assert peers.settings_from_config(config).role == ""


@pytest.fixture
def cloud_only(fake_client, tmp_path, monkeypatch):
    """The cloud as Render runs it now: the server profile (failover off),
    a history store made by history.open_store (origin from the profile)."""
    from kwil.config import load_config
    from kwil.failover import peers
    from kwil.history import open_store

    monkeypatch.setattr(peers, "_warned_disabled", True, raising=False)  # quiet
    monkeypatch.delenv("ASSISTANT_ROLE", raising=False)
    monkeypatch.delenv("FAILOVER_BACKENDS", raising=False)
    config = load_config(None, None, profile="server")
    config["webapp"]["sessions_file"] = str(tmp_path / "sessions.json")
    config["history"]["path"] = str(tmp_path / "history.sqlite")
    history = open_store(config)
    assistant = webapp.WebAssistant(
        lambda reg: ClaudeChat(client=fake_client, model="m", tools=reg),
        FakeTranscriber("hi"), PrintTTS(), history=history)
    guard = cloud.build_app(config, assistant, TOKEN)
    client = TestClient(at(PROXY, guard), base_url=APP, follow_redirects=False)
    return guard, client, history


def test_an_old_access_token_never_locks_the_phone_out_on_the_cloud(cloud_only, caplog):
    """Judge LOW 1: with failover off, a phone still sending its old hc1.
    token gets "not signed in", not a counted wrong guess, so it can't
    lock its own address out before the owner signs in again."""
    import logging

    guard, client, _ = cloud_only
    old, _ = TokenAuthority(TOKEN).mint("app")
    with caplog.at_level(logging.WARNING, logger="kwil"):
        for _ in range(8):
            r = client.get("/api/state", headers=bearer(old))
            assert r.status_code == 401 and r.json() == {"error": "not signed in"}
        for _ in range(3):
            assert client.post("/api/conversations/x/pin", headers=bearer(old + "x"),
                               json={}).status_code == 401
    assert not guard.lockout.blocked(PUBLIC)
    assert not any("wrong" in r.getMessage().lower() for r in caplog.records)
    assert client.post("/api/login", json={"token": TOKEN}, headers=hdr()).status_code == 200
    assert client.get("/api/state", headers=hdr()).status_code == 200
    # a voice socket with one is refused without counting either
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/voice",
                                      headers=hdr(Origin=APP, Authorization=f"Bearer {old}")):
            pass
    assert not guard.lockout.blocked(PUBLIC)
    # a real wrong WEB_TOKEN still counts
    for _ in range(5):
        client.get("/api/state", headers=bearer("x" * 43))
    assert guard.lockout.blocked(PUBLIC)


def test_sign_out_everywhere_on_the_cloud_is_labelled_cloud(cloud_only):
    """Judge LOW 3: the revoke event comes from the cloud, not "pc"."""
    from kwil.failover.sync import REVOKE_CONV

    _, client, history = cloud_only
    assert history.origin == "cloud"
    assert client.post("/api/login", json={"token": TOKEN}, headers=hdr()).status_code == 200
    assert client.post("/api/logout-all", headers=hdr(Origin=APP)).status_code == 200
    revoke = history.sync_log.by_conv(REVOKE_CONV)
    assert revoke and [e.origin for e in revoke] == ["cloud"]


def test_server_profile_with_the_shipped_config_boots_without_a_warning(
        tmp_path, monkeypatch, caplog):
    """Every Render boot warned "[failover] enabled is false ...": the
    shipped config.toml's backends template (three entries, every url
    empty) counted as something set. Only a backend with a url does."""
    import logging
    import shutil
    from pathlib import Path

    from kwil.config import load_config
    from kwil.failover import peers

    monkeypatch.setattr(peers, "_warned_disabled", False, raising=False)
    for var in ("ASSISTANT_ROLE", "APP_ORIGIN", "FAILOVER_BACKENDS"):
        monkeypatch.delenv(var, raising=False)
    shipped = tmp_path / "config.toml"  # away from any config.local.toml
    shutil.copy(Path(__file__).resolve().parents[1] / "config.toml", shipped)
    config = load_config(shipped, None, profile="server")
    assert config["failover"]["backends"]  # the empty-url template is there
    with caplog.at_level(logging.WARNING, logger="kwil"):
        assert peers.settings_from_config(config) == peers.FailoverSettings("", None, ())
    assert not caplog.records, [r.getMessage() for r in caplog.records]
    # a real url left in the list is still worth the warning
    config["failover"]["backends"] = [{"role": "pi", "url": ""},
                                      {"role": "pc", "url": "https://pc.t.ts.net"}]
    with caplog.at_level(logging.WARNING, logger="kwil"):
        peers.settings_from_config(config)
    assert len(caplog.records) == 1
