"""The phone app's "connect Google" sign-in (assistant/google_web.py).
Offline: Google's token endpoint is mocked (Flow.fetch_token), as in
tests/test_tools.py's headless sign-in test."""

import json
import logging
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from assistant import google_web, webapp  # noqa: E402
from assistant.chat import ClaudeChat  # noqa: E402
from assistant.config import load_config  # noqa: E402
from assistant.tts import PrintTTS  # noqa: E402
from tests.test_webapp import BASE, TOKEN, Clock, FakeTranscriber, login  # noqa: E402

ORIGIN = "https://pc.example.ts.net:8443"
TOKEN_JSON = {"token": "ya29.access-secret", "refresh_token": "1//refresh-secret-abcdefghijklmnop",
              "client_id": "cid.apps.googleusercontent.com", "client_secret": "csecret",
              "token_uri": "https://oauth2.googleapis.com/token",
              "scopes": ["https://www.googleapis.com/auth/calendar.events",
                         "https://www.googleapis.com/auth/gmail.readonly",
                         "https://www.googleapis.com/auth/gmail.compose"]}


@pytest.fixture
def setup(fake_client, tmp_path):
    client_file = tmp_path / "mom" / "google_client.json"
    client_file.parent.mkdir()
    client_file.write_text(json.dumps({"web": {  # a "Web application" client
        "client_id": "cid.apps.googleusercontent.com", "client_secret": "csecret",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [ORIGIN + "/google/callback"]}}))
    config = load_config(None, None)
    config["calendar"].update(credentials=str(client_file),
                              token=str(tmp_path / "mom" / "google_token.json"))
    config["webapp"].update(google_connect=True, public_origin=ORIGIN)
    config["notion"]["enabled"] = config["memory"]["enabled"] = False

    def chat_factory(registry):
        return ClaudeChat(client=fake_client, model="test-model", tools=registry)

    assistant = webapp.WebAssistant(chat_factory, FakeTranscriber(), PrintTTS(),
                                    clock=Clock())
    connect = google_web.GoogleConnect(config, assistant.tools)
    app = webapp.create_app(assistant, TOKEN, reap_interval=3600, google=connect,
                            limiter=webapp.LoginLimiter(per_ip=3, global_delay=0))
    return connect, assistant, TestClient(app, base_url=BASE), tmp_path / "mom" / "google_token.json"


def start(client) -> dict:
    r = client.post("/api/google/connect", json={})
    assert r.status_code == 200, r.text
    return parse_qs(urlparse(r.json()["url"]).query)


def fake_credentials():
    creds = MagicMock(granted_scopes=None)
    creds.to_json.return_value = json.dumps(TOKEN_JSON)
    return creds


def test_connect_needs_a_session_and_same_origin(setup):
    _, _, client, _ = setup
    assert client.post("/api/google/connect", json={}).status_code == 401
    assert client.get("/api/google/status").status_code == 401
    login(client)
    r = client.post("/api/google/connect", json={},
                    headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


def test_status_and_consent_url(setup):
    connect, _, client, _ = setup
    login(client)
    assert client.get("/api/google/status").json() == {"enabled": True, "connected": False}
    q = start(client)
    assert q["redirect_uri"] == [ORIGIN + "/google/callback"]
    assert q["access_type"] == ["offline"] and q["prompt"] == ["consent"]
    assert "code_challenge" in q and q["code_challenge_method"] == ["S256"]
    assert "gmail.compose" in q["scope"][0] and "calendar.events" in q["scope"][0]
    assert list(connect.pending) == q["state"]


def test_unknown_state_or_error_writes_nothing(setup):
    _, _, client, token = setup
    login(client)
    state = start(client)["state"][0]
    with patch("google_auth_oauthlib.flow.Flow.fetch_token") as fetch:
        r = client.get("/google/callback", params={"state": "made-up", "code": "c"},
                       follow_redirects=False)
        assert r.status_code == 400 and "Google" in r.text
        r = client.get("/google/callback", params={"state": state, "error": "access_denied"},
                       follow_redirects=False)
        assert r.status_code == 400
        # the denied state was used up
        r = client.get("/google/callback", params={"state": state, "code": "c"},
                       follow_redirects=False)
        assert r.status_code == 400
    fetch.assert_not_called()
    assert not token.exists()


def test_callback_saves_the_token_and_adds_the_tools(setup, caplog):
    connect, assistant, client, token = setup
    login(client)
    # a browser's session already exists: it must see the new tools too
    session = assistant.session(next(iter(assistant.sessions), "sid-x"))
    assert not session.registry.has("list_calendar_events")
    q = start(client)
    state = q["state"][0]
    caplog.set_level(logging.DEBUG)
    with patch("google_auth_oauthlib.flow.Flow.fetch_token") as fetch, \
         patch("google_auth_oauthlib.flow.Flow.credentials", new=fake_credentials()):
        r = client.get("/google/callback",
                       params={"state": state, "code": "4/secret-code", "scope": "x"},
                       follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    fetch.assert_called_once_with(code="4/secret-code")
    assert json.loads(token.read_text())["refresh_token"] == TOKEN_JSON["refresh_token"]
    for name in ("list_calendar_events", "create_calendar_event", "email_summary",
                 "draft_reply"):
        assert assistant.tools.has(name), name
        assert session.registry.has(name), name
    assert client.get("/api/google/status").json() == {"enabled": True, "connected": True}
    # replaying the same callback does nothing
    with patch("google_auth_oauthlib.flow.Flow.fetch_token") as again:
        r = client.get("/google/callback", params={"state": state, "code": "4/secret-code"},
                       follow_redirects=False)
    assert r.status_code == 400
    again.assert_not_called()
    info = [r.getMessage() for r in caplog.records
            if r.levelno >= logging.INFO and r.name.startswith("assistant")]
    assert any("Google account connected" in m for m in info)
    for secret in ("4/secret-code", state, "ya29", "refresh-secret", q["code_challenge"][0]):
        assert not any(secret in m for m in info), secret
    # a server access log line of the callback has its code masked in the file
    from assistant.config import redact
    assert "secret-code" not in redact(f"GET /google/callback?state={state}&code=4/secret-code")


def test_pending_states_are_bounded_and_expire(setup):
    connect, _, client, _ = setup
    login(client)
    states = [start(client)["state"][0] for _ in range(6)]
    assert list(connect.pending) == states[-google_web.MAX_PENDING:]
    connect.clock = lambda: 10 ** 9  # far past the TTL
    with patch("google_auth_oauthlib.flow.Flow.fetch_token") as fetch:
        r = client.get("/google/callback", params={"state": states[-1], "code": "c"},
                       follow_redirects=False)
    assert r.status_code == 400 and not connect.pending
    fetch.assert_not_called()


def test_not_set_up_without_public_origin(setup):
    connect, _, client, _ = setup
    connect.config["webapp"]["public_origin"] = ""
    login(client)
    assert client.get("/api/google/status").json()["enabled"] is False
    assert client.post("/api/google/connect", json={}).status_code == 503


def test_routes_absent_when_google_connect_is_off(fake_client):
    assistant = webapp.WebAssistant(lambda r: ClaudeChat(client=fake_client, tools=r),
                                    FakeTranscriber(), PrintTTS(), clock=Clock())
    client = TestClient(webapp.create_app(assistant, TOKEN, reap_interval=3600),
                        base_url=BASE)
    login(client)
    assert client.get("/api/google/status").status_code == 404
    assert client.get("/google/callback", params={"state": "s", "code": "c"}).status_code == 404
