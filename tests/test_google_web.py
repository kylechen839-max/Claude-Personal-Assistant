"""The phone app's "connect Google" sign-in (kwil/google_web.py).
Offline: Google's token endpoint is mocked (Flow.fetch_token), as in
tests/test_tools.py's headless sign-in test."""

import json
import logging
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from kwil import google_web, webapp  # noqa: E402
from kwil.chat import ClaudeChat  # noqa: E402
from kwil.config import load_config  # noqa: E402
from kwil.tts import PrintTTS  # noqa: E402
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
    assert client.get("/api/google/status").json() == {"enabled": True, "connected": False,
                                                       "disconnect": False,
                                                       "gmail": True, "note": None,
                                                       "needs_reconnect": False,
                                                       "missing": [],
                                                       "server_owner": False}
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
    session = assistant.session(
        next(iter(assistant.sessions), None) or client.app.state.web.store.create())
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
    assert client.get("/api/google/status").json() == {"enabled": True, "connected": True,
                                                       "disconnect": False,
                                                       "gmail": True, "note": None,
                                                       "needs_reconnect": False,
                                                       # this token predates the extras and Drive
                                                       "missing": [
        "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
        "https://www.googleapis.com/auth/calendar.calendars.readonly",
        "https://www.googleapis.com/auth/drive.readonly"],
                                                       "server_owner": False}
    # replaying the same callback does nothing
    with patch("google_auth_oauthlib.flow.Flow.fetch_token") as again:
        r = client.get("/google/callback", params={"state": state, "code": "4/secret-code"},
                       follow_redirects=False)
    assert r.status_code == 400
    again.assert_not_called()
    info = [r.getMessage() for r in caplog.records
            if r.levelno >= logging.INFO and r.name.startswith("kwil")]
    assert any("Google account connected" in m for m in info)
    for secret in ("4/secret-code", state, "ya29", "refresh-secret", q["code_challenge"][0]):
        assert not any(secret in m for m in info), secret
    # a server access log line of the callback has its code masked in the file
    from kwil.config import redact
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


# --- the owner's own Google on the cloud server ([webapp] owner_google) -------------

SERVER = "https://heykwil.example.com"
# Kyle's Desktop ("installed") client, as GOOGLE_CLIENT_JSON on Render: it
# can't take an https redirect, so the owner's sign-in must never use it
INSTALLED = {"installed": {
    "client_id": "desktop.apps.googleusercontent.com", "client_secret": "desktop-secret",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "redirect_uris": ["http://localhost"]}}
WEB = {"web": {  # GOOGLE_WEB_CLIENT_JSON: the family's Web application client
    "client_id": "web.apps.googleusercontent.com", "client_secret": "web-secret",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "redirect_uris": [SERVER + "/google/callback"]}}
ALL_SCOPES = ["https://www.googleapis.com/auth/calendar.events",
              "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
              "https://www.googleapis.com/auth/calendar.calendars.readonly",
              "https://www.googleapis.com/auth/gmail.readonly",
              "https://www.googleapis.com/auth/gmail.compose"]
# the old GOOGLE_TOKEN_JSON secret, made on the PC with the Desktop client
ENV_TOKEN = {"token": "ya29.env-access", "refresh_token": "1//env-refresh-secret",
             "client_id": "desktop.apps.googleusercontent.com",
             "client_secret": "desktop-secret",
             "token_uri": "https://oauth2.googleapis.com/token", "scopes": ALL_SCOPES,
             "expiry": "2099-01-01T00:00:00Z"}  # valid: nothing to refresh
GOOGLE_TOOLS = {"list_calendar_events", "create_calendar_event", "email_summary",
                "draft_reply"}


@pytest.fixture
def server(fake_client, tmp_path, monkeypatch):
    """The cloud server (profile "server") with Kyle's old token in
    GOOGLE_TOKEN_JSON, his Desktop client in GOOGLE_CLIENT_JSON and the
    family's Web client in GOOGLE_WEB_CLIENT_JSON, as on Render."""
    from google.oauth2.credentials import Credentials

    from kwil import account
    from kwil import family as family_mod
    from kwil.cli import register_google
    from kwil.config import ENV_OVERRIDES

    for var in ENV_OVERRIDES:  # none of the caller's settings
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("GOOGLE_PUBLIC_CLIENT_JSON", raising=False)
    monkeypatch.setenv("GOOGLE_TOKEN_JSON", json.dumps(ENV_TOKEN))
    monkeypatch.setenv("GOOGLE_CLIENT_JSON", json.dumps(INSTALLED))
    monkeypatch.setenv("GOOGLE_WEB_CLIENT_JSON", json.dumps(WEB))
    revoked = []
    monkeypatch.setattr(account, "revoke_google", revoked.append)

    def refresh(self, request):
        raise AssertionError("a test tried to refresh a Google token")

    monkeypatch.setattr(Credentials, "refresh", refresh)
    config = load_config(None, None, profile="server")
    data = tmp_path / "data"
    data.mkdir()
    config["calendar"].update(token=str(data / "google_token.json"),
                              credentials=str(tmp_path / "no-credentials.json"))
    config["webapp"].update(public_origin=SERVER, sessions_file="",
                            waitlist_file=str(data / "waitlist.json"))
    config["family"]["dir"] = str(data / "users")
    for section in ("notion", "memory", "student"):
        config[section]["enabled"] = False
    fam = family_mod.Family(config, client=fake_client, user_client=lambda key: fake_client)
    assistant = webapp.WebAssistant(
        lambda reg: ClaudeChat(client=fake_client, model="m", tools=reg),
        FakeTranscriber(), PrintTTS(), clock=Clock(), family=fam)
    register_google(assistant.tools, config, output_fn=lambda m: None)  # as at startup
    connect = google_web.from_config(config, assistant)
    app = webapp.create_app(assistant, TOKEN, reap_interval=3600, google=connect,
                            limiter=webapp.LoginLimiter(per_ip=3, global_delay=0))

    class S:
        pass

    s = S()
    s.__dict__.update(config=config, fam=fam, assistant=assistant, connect=connect, app=app,
                      token=data / "google_token.json", revoked=revoked)
    yield s
    fam.close()


def owner_client(s):
    client = TestClient(s.app, base_url=BASE)
    login(client)
    return client


def google_answers(refresh_token="1//connected-refresh-secret"):
    """Flow.fetch_token as Google's token endpoint answers it: the real
    Flow then builds the credentials (client id and secret from its own
    client config), so the test sees what the server really saves."""
    import time as _time

    from google_auth_oauthlib.flow import Flow

    def fetch(self, **kwargs):
        self.oauth2session.token = {
            "access_token": "ya29.connected-access", "refresh_token": refresh_token,
            "token_type": "Bearer", "expires_in": 3600,
            "expires_at": _time.time() + 3600, "scope": ALL_SCOPES}

    return patch.object(Flow, "fetch_token", autospec=True, side_effect=fetch)


def tool_refresh_token(s, name="list_calendar_events"):
    from tests.test_family_google import built_credentials

    return built_credentials(s.assistant.tools.tools[name]).refresh_token


def owner_connects(s, kyle, code="4/owner-code"):
    """Kyle's Connect Google, start to callback; returns the consent URL's query."""
    r = kyle.post("/api/google/connect", json={})
    assert r.status_code == 200, r.text
    q = parse_qs(urlparse(r.json()["url"]).query)
    with google_answers():
        r = kyle.get("/google/callback", params={"state": q["state"][0], "code": code},
                     follow_redirects=False)
    assert r.status_code == 303, r.text
    return q


def test_owner_connects_on_the_server_and_the_connected_token_wins(server, caplog):
    s = server
    # before: the env token (GOOGLE_TOKEN_JSON) serves Calendar and Gmail
    assert GOOGLE_TOOLS <= set(s.assistant.tools.tools)
    assert tool_refresh_token(s) == ENV_TOKEN["refresh_token"]
    kyle = owner_client(s)
    st = kyle.get("/api/google/status").json()
    assert st["enabled"] is True and st["connected"] is True  # the env token
    assert st["disconnect"] is False and st["server_owner"] is True
    r = kyle.post("/api/google/connect", json={})
    assert r.status_code == 200, r.text
    q = parse_qs(urlparse(r.json()["url"]).query)
    # the Web client, never the Desktop one; back to this one hostname
    assert q["client_id"] == [WEB["web"]["client_id"]]
    assert q["redirect_uri"] == [SERVER + "/google/callback"]
    assert "gmail.compose" in q["scope"][0]
    state = q["state"][0]
    caplog.set_level(logging.DEBUG)
    with google_answers():
        r = kyle.get("/google/callback", params={"state": state, "code": "4/owner-code"},
                     follow_redirects=False)
    assert r.status_code == 303, r.text
    saved = json.loads(s.token.read_text())
    assert saved["refresh_token"] == "1//connected-refresh-secret"
    # the saved token carries the Web client's id and secret, so a refresh
    # never borrows the Desktop client (token_info's setdefault)
    assert saved["client_id"] == WEB["web"]["client_id"]
    assert saved["client_secret"] == WEB["web"]["client_secret"]
    import sys
    if sys.platform != "win32":
        assert s.token.stat().st_mode & 0o077 == 0
    # the tools now use the connected token, though GOOGLE_TOKEN_JSON is set
    for name in ("list_calendar_events", "email_summary"):
        assert tool_refresh_token(s, name) == "1//connected-refresh-secret", name
    st = kyle.get("/api/google/status").json()
    assert st["connected"] is True and st["disconnect"] is True
    info = [r.getMessage() for r in caplog.records
            if r.levelno >= logging.INFO and r.name.startswith("kwil")]
    assert any("Google account connected" in m for m in info)
    for secret in ("4/owner-code", state, "ya29", "refresh-secret", "web-secret",
                   "desktop-secret", q["code_challenge"][0]):
        assert not any(secret in m for m in info), secret


def test_without_a_connected_token_the_env_token_is_used(server):
    """No file, or one that can't be read: GOOGLE_TOKEN_JSON as before, and
    nothing for the owner to disconnect here."""
    s = server
    kyle = owner_client(s)
    st = kyle.get("/api/google/status").json()
    assert st == {"enabled": True, "connected": True, "disconnect": False,
                  "gmail": True, "note": None, "needs_reconnect": False,
                  # GOOGLE_TOKEN_JSON from before Drive: Reconnect adds it
                  "missing": ["https://www.googleapis.com/auth/drive.readonly"],
                  "server_owner": True}
    assert kyle.post("/api/google/disconnect", json={}).status_code == 404
    assert tool_refresh_token(s) == ENV_TOKEN["refresh_token"]
    from kwil.cli import register_google
    from kwil.tools import ToolRegistry
    from kwil.tools.google_auth import owner_connected
    for junk in ("{not json", "[]", "{}"):  # unreadable or empty: still the env's
        s.token.write_text(junk)
        assert not owner_connected(s.config), junk
        fresh = ToolRegistry()
        register_google(fresh, s.config, output_fn=lambda m: None)
        from tests.test_family_google import built_credentials
        creds = built_credentials(fresh.tools["list_calendar_events"])
        assert creds.refresh_token == ENV_TOKEN["refresh_token"], junk


def test_owner_disconnect_revokes_deletes_and_falls_back_to_env(server, caplog):
    s = server
    kyle = owner_client(s)
    owner_connects(s, kyle)
    assert tool_refresh_token(s) == "1//connected-refresh-secret"
    caplog.set_level(logging.DEBUG)
    r = kyle.post("/api/google/disconnect", json={})
    assert r.status_code == 200 and r.json() == {"ok": True, "connected": False}
    assert s.revoked == ["1//connected-refresh-secret"]  # revoked, then deleted
    assert not s.token.exists()
    # the tools are back on GOOGLE_TOKEN_JSON
    assert GOOGLE_TOOLS <= set(s.assistant.tools.tools)
    for name in ("list_calendar_events", "email_summary"):
        assert tool_refresh_token(s, name) == ENV_TOKEN["refresh_token"], name
    st = kyle.get("/api/google/status").json()
    assert st["connected"] is True and st["disconnect"] is False
    assert kyle.post("/api/google/disconnect", json={}).status_code == 404  # nothing left
    info = [r.getMessage() for r in caplog.records
            if r.levelno >= logging.INFO and r.name.startswith("kwil")]
    assert any("revoked the owner's Google grant" in m for m in info)
    assert not any("refresh-secret" in m or "ya29" in m for m in info)


def test_owner_disconnect_without_env_token_removes_the_tools(server, monkeypatch):
    """No GOOGLE_TOKEN_JSON to fall back on: Calendar and Gmail go, in place
    (every open conversation shares the registry's dict)."""
    s = server
    monkeypatch.delenv("GOOGLE_TOKEN_JSON")
    kyle = owner_client(s)
    owner_connects(s, kyle)
    shared = s.assistant.tools.tools
    assert GOOGLE_TOOLS <= set(shared)
    assert kyle.post("/api/google/disconnect", json={}).status_code == 200
    assert s.assistant.tools.tools is shared
    assert not GOOGLE_TOOLS & set(shared)
    assert kyle.get("/api/google/status").json()["connected"] is False


def test_owner_disconnect_when_the_revoke_fails_or_the_file_is_corrupt(server, monkeypatch,
                                                                       caplog):
    from kwil import account

    s = server
    kyle = owner_client(s)
    owner_connects(s, kyle)

    def refuse(token):
        raise ConnectionError(f"Google is down ({token})")  # never logged

    monkeypatch.setattr(account, "revoke_google", refuse)
    caplog.set_level(logging.DEBUG)
    assert kyle.post("/api/google/disconnect", json={}).status_code == 200
    assert not s.token.exists()  # deleted anyway
    assert tool_refresh_token(s) == ENV_TOKEN["refresh_token"]
    logged = [r.getMessage() for r in caplog.records if r.name.startswith("kwil")]
    assert any("revoking the owner's Google grant failed (ConnectionError)" in m
               for m in logged)
    assert not any("refresh-secret" in m for m in logged)
    # a corrupt file: nothing to revoke, but it still goes
    s.token.write_text("[1, 2")
    s.revoked.clear()
    monkeypatch.setattr(account, "revoke_google", s.revoked.append)
    assert kyle.get("/api/google/status").json()["disconnect"] is True
    assert kyle.post("/api/google/disconnect", json={}).status_code == 200
    assert s.revoked == [] and not s.token.exists()


def test_a_connect_finishing_during_the_disconnects_revoke_is_kept(server, monkeypatch):
    """The revoke runs outside the lock: a new sign-in saved meanwhile must
    survive (only the token that was revoked is deleted)."""
    from kwil import account

    s = server
    kyle = owner_client(s)
    owner_connects(s, kyle)
    real = account.revoke_google_token_file
    newer = {**ENV_TOKEN, "refresh_token": "1//newer-refresh-secret"}

    def revoke_then_reconnect(path, *args, **kwargs):
        real(path, *args, **kwargs)
        from kwil.tools.google_auth import write_private
        write_private(path, json.dumps(newer))  # Connect's callback, mid-revoke

    monkeypatch.setattr(account, "revoke_google_token_file", revoke_then_reconnect)
    assert kyle.post("/api/google/disconnect", json={}).status_code == 200
    assert s.revoked == ["1//connected-refresh-secret"]  # only the old one
    assert json.loads(s.token.read_text())["refresh_token"] == "1//newer-refresh-secret"
    assert tool_refresh_token(s) == "1//newer-refresh-secret"
    assert kyle.get("/api/google/status").json()["disconnect"] is True


def test_a_family_member_cant_start_or_disconnect_the_owners_google(server):
    s = server
    kyle = owner_client(s)
    owner_connects(s, kyle)
    owners = s.token.read_text()
    _, invite = s.fam.invite("Mom", "en", tier="family")
    mom = TestClient(s.app, base_url=BASE)
    assert mom.post("/api/invite/accept", json={"invite": invite}).status_code == 200
    mom_id = next(u["id"] for u in s.fam.members() if u["name"] == "Mom")
    st = mom.get("/api/google/status").json()
    assert st["connected"] is False and st["server_owner"] is False  # not Kyle's
    # her Disconnect is about her own account: Kyle's file and grant stay
    assert mom.post("/api/google/disconnect", json={}).status_code == 200
    assert s.token.read_text() == owners and s.revoked == []
    # her Connect is her own sign-in: it writes to her folder, never Kyle's file
    r = mom.post("/api/google/connect", json={})
    assert r.status_code == 200
    state = parse_qs(urlparse(r.json()["url"]).query)["state"][0]
    assert s.connect.pending[state].user == mom_id
    with google_answers("1//mom-refresh-secret"):
        r = mom.get("/google/callback", params={"state": state, "code": "4/c"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert s.token.read_text() == owners
    assert "mom-refresh" in s.fam.google_token(mom_id).read_text()
    assert tool_refresh_token(s) == "1//connected-refresh-secret"


def test_owner_callback_with_another_sessions_state_is_refused(server):
    s = server
    kyle = owner_client(s)
    r = kyle.post("/api/google/connect", json={})
    state = parse_qs(urlparse(r.json()["url"]).query)["state"][0]
    other = owner_client(s)  # another browser, its own binding cookie
    assert other.post("/api/google/connect", json={}).status_code == 200
    with google_answers() as fetch:
        r = other.get("/google/callback", params={"state": state, "code": "4/c"},
                      follow_redirects=False)
    assert r.status_code == 400 and not fetch.called
    assert not s.token.exists()
    assert tool_refresh_token(s) == ENV_TOKEN["refresh_token"]


def test_doctor_reports_the_connected_token_then_the_env_fallback(server):
    from kwil.doctor import check_google_env

    s = server
    [check] = check_google_env(s.config)
    assert check.name == "GOOGLE_TOKEN_JSON" and check.ok
    owner_connects(s, owner_client(s))
    [check] = check_google_env(s.config)
    assert check.name == "Google account" and check.ok and "in the app" in check.detail
    import os
    env = {k: v for k, v in os.environ.items() if k != "GOOGLE_TOKEN_JSON"}
    s.token.unlink()
    [check] = check_google_env(s.config, env)
    assert not check.ok and "Connect Google account" in check.detail


def test_with_owner_google_off_the_env_token_still_wins(tmp_path, monkeypatch):
    """A PC (owner_google off): GOOGLE_TOKEN_JSON is read before token.json,
    as before, even with a token file present."""
    from kwil.cli import register_google
    from kwil.tools import ToolRegistry
    from tests.test_family_google import built_credentials

    monkeypatch.setenv("GOOGLE_TOKEN_JSON", json.dumps(ENV_TOKEN))
    monkeypatch.delenv("ASSISTANT_PROFILE", raising=False)
    config = load_config(None, None, profile="")
    assert config["webapp"]["owner_google"] is False
    token = tmp_path / "token.json"
    token.write_text(json.dumps({**ENV_TOKEN, "refresh_token": "1//file-refresh"}))
    config["calendar"].update(token=str(token), credentials=str(tmp_path / "none.json"),
                              save_token=False)
    registry = ToolRegistry()
    register_google(registry, config, output_fn=lambda m: None)
    from google.oauth2.credentials import Credentials
    with patch.object(Credentials, "refresh", side_effect=AssertionError("refresh")):
        creds = built_credentials(registry.tools["list_calendar_events"])
    assert creds.refresh_token == ENV_TOKEN["refresh_token"]
    assert registry.tools["list_calendar_events"].handler.__self__.in_app is False


def test_the_server_starts_with_an_inherited_google_token_json(tmp_path, monkeypatch,
                                                               restore_root_logging):
    """The server profile turns owner_google on, not google_connect: the
    GOOGLE_TOKEN_JSON Render sets is never refused as "leaked"."""
    from kwil import cli, cloud
    from kwil.config import ISOLATED_ENV_VARS

    for var in ISOLATED_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ASSISTANT_PROFILE", "server")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("GOOGLE_TOKEN_JSON", json.dumps(ENV_TOKEN))
    served = []
    monkeypatch.setattr(cloud, "serve", lambda config, **kw: served.append(config) or 0)
    assert cli.main(["--mode", "web", "--config", "none.toml"]) == 0
    assert served and served[-1]["webapp"]["owner_google"] is True
    assert served[-1]["calendar"]["token"] == "/data/google_token.json"
