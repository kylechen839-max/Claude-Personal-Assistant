"""The phone app's "Connect Spotify" (assistant/spotify_web.py), owner only.
Offline: Spotify's token endpoint is a fake (tests/test_spotify.py)."""

import json
import logging
import os
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from assistant import family as family_mod, spotify_web, webapp  # noqa: E402
from assistant.chat import ClaudeChat  # noqa: E402
from assistant.config import load_config  # noqa: E402
from assistant.tools import spotify as sp  # noqa: E402
from assistant.tts import PrintTTS  # noqa: E402
from tests.test_spotify import CID, FakeHTTP, Resp  # noqa: E402
from tests.test_webapp import BASE, TOKEN, Clock, FakeTranscriber, login  # noqa: E402

ORIGIN = "https://pc.example.ts.net:8443"
TOKEN_REPLY = {"access_token": "BQ-access-secret", "token_type": "Bearer",
               "expires_in": 3600, "refresh_token": "AQ-refresh-secret",
               "scope": " ".join(sp.SCOPES)}


@pytest.fixture
def setup(fake_client, tmp_path, monkeypatch):
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", CID)
    config = load_config(None, None)
    config["family"].update(enabled=True, dir=str(tmp_path / "users"))
    config["spotify"].update(enabled=True, token=str(tmp_path / "data" / "spotify.json"))
    config["webapp"]["public_origin"] = ORIGIN
    fam = family_mod.Family(config, client=fake_client)
    assistant = webapp.WebAssistant(
        lambda r: ClaudeChat(client=fake_client, model="test-model", tools=r),
        FakeTranscriber(), PrintTTS(), clock=Clock(), family=fam)
    http = FakeHTTP({("POST", sp.TOKEN_URL): Resp(200, TOKEN_REPLY)})
    connect = spotify_web.SpotifyConnect(config, assistant.tools, http=http)
    app = webapp.create_app(assistant, TOKEN, reap_interval=3600, spotify=connect,
                            limiter=webapp.LoginLimiter(per_ip=3, global_delay=0))
    client = TestClient(app, base_url=BASE)
    yield connect, assistant, client, http, fam, tmp_path / "data" / "spotify.json"
    fam.close()


def start(client) -> dict:
    r = client.post("/api/spotify/connect", json={})
    assert r.status_code == 200, r.text
    return parse_qs(urlparse(r.json()["url"]).query)


def test_needs_the_owner_signed_in(setup):
    _, _, client, *_ = setup
    assert client.get("/api/spotify/status").status_code == 401
    assert client.post("/api/spotify/connect", json={}).status_code == 401
    login(client)
    r = client.post("/api/spotify/connect", json={},
                    headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


def test_status_and_consent_url(setup):
    connect, _, client, *_ = setup
    login(client)
    assert client.get("/api/spotify/status").json() == {"enabled": True, "connected": False}
    q = start(client)
    assert q["redirect_uri"] == [ORIGIN + "/spotify/callback"]
    assert q["client_id"] == [CID] and q["code_challenge_method"] == ["S256"]
    assert list(connect.pending) == q["state"]
    verifier = connect.pending[q["state"][0]][0]
    assert q["code_challenge"] == [sp.challenge(verifier)]


def test_unknown_state_or_denied_writes_nothing(setup):
    _, _, client, http, _, token = setup
    login(client)
    state = start(client)["state"][0]
    r = client.get("/spotify/callback", params={"state": "made-up", "code": "c"},
                   follow_redirects=False)
    assert r.status_code == 400 and "Spotify" in r.text
    r = client.get("/spotify/callback", params={"state": state, "error": "access_denied"},
                   follow_redirects=False)
    assert r.status_code == 400
    r = client.get("/spotify/callback", params={"state": state, "code": "c"},
                   follow_redirects=False)
    assert r.status_code == 400  # the denied state was used up
    assert not http.calls and not token.exists()


def test_callback_saves_the_token_privately_and_adds_the_tools(setup, caplog):
    connect, assistant, client, http, _, token = setup
    login(client)
    session = assistant.session(client.app.state.web.store.create())
    assert not session.registry.has("spotify_play")
    q = start(client)
    state = q["state"][0]
    verifier = connect.pending[state][0]
    caplog.set_level(logging.DEBUG)
    r = client.get("/spotify/callback", params={"state": state, "code": "AQD-secret-code"},
                   follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    form = http.calls[0].data
    assert form == {"grant_type": "authorization_code", "code": "AQD-secret-code",
                    "redirect_uri": ORIGIN + "/spotify/callback", "client_id": CID,
                    "code_verifier": verifier}
    saved = json.loads(token.read_text())
    assert saved["refresh_token"] == "AQ-refresh-secret"
    if os.name != "nt":
        assert token.stat().st_mode & 0o777 == 0o600
    assert assistant.tools.has("spotify_play") and session.registry.has("spotify_pause")
    assert client.get("/api/spotify/status").json() == {"enabled": True, "connected": True}
    r = client.get("/spotify/callback", params={"state": state, "code": "AQD-secret-code"},
                   follow_redirects=False)
    assert r.status_code == 400 and len(http.calls) == 1  # replay: nothing
    info = [r.getMessage() for r in caplog.records
            if r.levelno >= logging.INFO and r.name.startswith("assistant")]
    assert any("Spotify connected" in m for m in info)
    for secret in ("AQD-secret-code", state, "BQ-access", "AQ-refresh", verifier):
        assert not any(secret in m for m in info), secret


def test_a_refused_exchange_writes_nothing(setup):
    connect, assistant, client, http, _, token = setup
    http.routes[("POST", sp.TOKEN_URL)] = Resp(400, {"error": "invalid_grant"})
    login(client)
    state = start(client)["state"][0]
    r = client.get("/spotify/callback", params={"state": state, "code": "c"},
                   follow_redirects=False)
    assert r.status_code == 400 and not token.exists()
    assert not assistant.tools.has("spotify_play")


def test_pending_states_are_bounded_and_expire(setup):
    connect, _, client, http, *_ = setup
    login(client)
    states = [start(client)["state"][0] for _ in range(6)]
    assert list(connect.pending) == states[-spotify_web.MAX_PENDING:]
    connect.clock = lambda: 10 ** 9
    r = client.get("/spotify/callback", params={"state": states[-1], "code": "c"},
                   follow_redirects=False)
    assert r.status_code == 400 and not connect.pending and not http.calls


def test_not_set_up_without_client_id_or_origin(setup, monkeypatch):
    connect, _, client, *_ = setup
    login(client)
    monkeypatch.delenv("SPOTIFY_CLIENT_ID")
    assert client.get("/api/spotify/status").json()["enabled"] is False
    assert client.post("/api/spotify/connect", json={}).status_code == 503
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", CID)
    connect.config["webapp"]["public_origin"] = ""
    assert client.post("/api/spotify/connect", json={}).status_code == 503


def test_family_gets_nothing(setup):
    connect, assistant, client, http, fam, token = setup
    login(client)
    state = start(client)["state"][0]
    assert client.get("/spotify/callback", params={"state": state, "code": "c"},
                      follow_redirects=False).status_code == 303
    _, invite = fam.invite("Mom", "zh")
    mom = TestClient(client.app, base_url=BASE)
    assert mom.post("/api/invite/accept", json={"invite": invite}).status_code == 200
    assert mom.get("/api/spotify/status").status_code == 404
    assert mom.post("/api/spotify/connect", json={}).status_code == 404
    uid = next(iter(fam.users))
    assert not any(n.startswith("spotify") for n in fam.context(uid).registry.tools)
    assert mom.get("/api/state").status_code == 200  # her session, made on first use
    mom_session = next(s for s in assistant.sessions.values() if s.user == uid)
    assert not any(n.startswith("spotify") for n in mom_session.registry.tools)


def test_routes_absent_when_spotify_is_off(fake_client):
    assistant = webapp.WebAssistant(lambda r: ClaudeChat(client=fake_client, tools=r),
                                    FakeTranscriber(), PrintTTS(), clock=Clock())
    client = TestClient(webapp.create_app(assistant, TOKEN, reap_interval=3600),
                        base_url=BASE)
    login(client)
    assert client.get("/api/spotify/status").status_code == 404
    assert client.get("/spotify/callback", params={"state": "s", "code": "c"}).status_code == 404


def test_page_has_the_button_and_script(fake_client):
    assistant = webapp.WebAssistant(lambda r: ClaudeChat(client=fake_client, tools=r),
                                    FakeTranscriber(), PrintTTS(), clock=Clock())
    client = TestClient(webapp.create_app(assistant, TOKEN, reap_interval=3600),
                        base_url=BASE)
    page = client.get("/").text
    assert 'id="spotify-connect"' in page and 'src="/spotify.js"' in page
    assert page.index("/spotify.js") < page.index("/app.js")
    assert client.get("/spotify.js").status_code == 200
    sw = (webapp.STATIC / "sw.js").read_text(encoding="utf-8")
    assert '"/spotify.js"' in sw
    from assistant import cloud

    assert "/spotify.js" in cloud.PUBLIC_GET
