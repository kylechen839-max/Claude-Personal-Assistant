"""A revoked or expired Google sign-in shows as "expired, reconnect", not
"connected" (google_auth.needs_reconnect, google_web status, doctor).

Offline: Credentials.refresh is patched (Google's refusal is a RefreshError
as google-auth raises it); nothing reaches Google."""

import json
import logging

import pytest

pytest.importorskip("fastapi")

from kwil import doctor  # noqa: E402
from kwil.tools import google_auth  # noqa: E402
from kwil.tools.google_auth import (OWNER, GoogleSignInError,  # noqa: E402
                                    get_credentials, needs_reconnect)
# the fixtures: the cloud server with GOOGLE_TOKEN_JSON, and family members
from tests.test_family_google import connected, g, no_google_network  # noqa: E402,F401
from tests.test_google_web import (ENV_TOKEN, owner_client,  # noqa: E402,F401
                                   owner_connects, server)

REFUSED = "invalid_grant: Token has been expired or revoked."
# what google-auth's token endpoint error carries (never logged)
REFUSED_BODY = {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}


def _refused(self, request):
    from google.auth.exceptions import RefreshError

    raise RefreshError(REFUSED, REFUSED_BODY)


def _busy(self, request):  # Google's side failing: says nothing about the grant
    from google.auth.exceptions import RefreshError

    raise RefreshError("server_error: try again", {"error": "server_error"},
                       retryable=True)


def _refreshed(self, request):
    import datetime

    self.token = "ya29.fresh-access"
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    self.expiry = now + datetime.timedelta(hours=1)  # naive UTC, as google-auth keeps it


def stale(info: dict) -> dict:
    """The token without its expiry: using it needs a refresh."""
    return {k: v for k, v in info.items() if k != "expiry"}


def owner_stale_token(monkeypatch):
    monkeypatch.setenv("GOOGLE_TOKEN_JSON", json.dumps(stale(ENV_TOKEN)))


def use_calendar(registry):
    """The calendar tool, as a turn would call it (its service built anew)."""
    tool = registry.tools["list_calendar_events"]
    tool.handler.__self__._service = None
    return registry.dispatch("list_calendar_events", {"day": "today"})


# --- google_auth: who must reconnect ------------------------------------------------

def test_a_refused_refresh_marks_that_account_and_a_later_one_clears_it(
        tmp_path, monkeypatch, caplog):
    from google.oauth2.credentials import Credentials

    token = tmp_path / "token.json"
    token.write_text(json.dumps(stale(ENV_TOKEN)))
    monkeypatch.setattr(Credentials, "refresh", _refused)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(GoogleSignInError) as e:
        get_credentials("", token, save_token=False, environ={}, in_app=True,
                        account="u1")
    assert e.value.reason == "invalid_grant"
    assert needs_reconnect("u1") and not needs_reconnect(OWNER)
    assert not needs_reconnect("u2")  # per person
    monkeypatch.setattr(Credentials, "refresh", _refreshed)
    get_credentials("", token, save_token=False, environ={}, in_app=True, account="u1")
    assert not needs_reconnect("u1")
    info = [r.getMessage() for r in caplog.records
            if r.levelno >= logging.INFO and r.name.startswith("kwil")]
    assert any("needs reconnecting for u1" in m for m in info)
    assert any("works again for u1" in m for m in info)
    for secret in ("refresh-secret", "ya29", "Token has been expired", "desktop-secret"):
        assert not any(secret in m for m in info), secret


def test_the_owner_is_marked_too_and_googles_own_trouble_marks_no_one(
        tmp_path, monkeypatch):
    from google.oauth2.credentials import Credentials

    token = tmp_path / "token.json"
    token.write_text(json.dumps(stale(ENV_TOKEN)))
    monkeypatch.setattr(Credentials, "refresh", _busy)
    with pytest.raises(GoogleSignInError):
        get_credentials("", token, save_token=False, environ={})
    assert not needs_reconnect(OWNER)  # retryable: the grant may be fine
    monkeypatch.setattr(Credentials, "refresh", _refused)
    with pytest.raises(GoogleSignInError):
        get_credentials("", token, save_token=False, environ={})
    assert needs_reconnect(OWNER)
    assert list(google_auth._reconnect) == [(OWNER, "file")]  # a token file
    assert isinstance(google_auth._reconnect[(OWNER, "file")], float)  # when, nothing else
    assert needs_reconnect(OWNER, "file") and not needs_reconnect(OWNER, "env")


def test_the_flags_are_bounded(monkeypatch):
    monkeypatch.setattr(google_auth, "MAX_RECONNECT", 3)
    for uid in ("a", "b", "c", "d"):
        google_auth.mark_refresh_failed(uid)
    assert [who for who, _ in google_auth._reconnect] == ["b", "c", "d"]  # the oldest went


# --- the status: the owner on the cloud server ---------------------------------------

def test_owner_status_says_reconnect_after_google_refuses_then_connect_clears(
        server, monkeypatch, caplog):
    """The bug on heykwil.com: GOOGLE_TOKEN_JSON revoked, Settings still
    said "connected" with nothing to tap."""
    from google.oauth2.credentials import Credentials

    s = server
    kyle = owner_client(s)
    assert kyle.get("/api/google/status").json()["needs_reconnect"] is False
    owner_stale_token(monkeypatch)
    monkeypatch.setattr(Credentials, "refresh", _refused)
    caplog.set_level(logging.DEBUG)
    result, is_error = use_calendar(s.assistant.tools)
    assert is_error and "revoked" in result
    st = kyle.get("/api/google/status").json()
    # a token is still there, Google refuses it: Reconnect, from the env token too
    assert st["connected"] is True and st["needs_reconnect"] is True
    assert st["enabled"] is True and st["server_owner"] is True
    info = [r.getMessage() for r in caplog.records
            if r.levelno >= logging.INFO and r.name.startswith("kwil")]
    assert not any("env-refresh-secret" in m or "ya29" in m
                   or "Token has been expired" in m for m in info)
    # Reconnect: the same connect flow; its token file wins, the flag goes
    owner_connects(s, kyle)
    st = kyle.get("/api/google/status").json()
    assert st["connected"] is True and st["needs_reconnect"] is False
    assert st["disconnect"] is True


def test_owner_disconnect_clears_it_too(server, monkeypatch):
    from google.oauth2.credentials import Credentials

    s = server
    kyle = owner_client(s)
    owner_connects(s, kyle)
    s.token.write_text(json.dumps(stale(json.loads(s.token.read_text()))))
    monkeypatch.setattr(Credentials, "refresh", _refused)
    use_calendar(s.assistant.tools)
    assert kyle.get("/api/google/status").json()["needs_reconnect"] is True
    assert kyle.post("/api/google/disconnect", json={}).status_code == 200
    assert not needs_reconnect(OWNER)
    st = kyle.get("/api/google/status").json()
    assert st["needs_reconnect"] is False and st["connected"] is True  # the env's


def test_owner_can_connect_with_only_the_servers_own_address(server, monkeypatch):
    """No PUBLIC_ORIGIN, but the cloud knows its address (APP_ORIGIN with
    [failover]): the owner's Connect works where a family member's does,
    and both come back to the cloud, never to a peer listed in
    FAILOVER_BACKENDS (that list once overwrote this machine's role)."""
    s = server
    monkeypatch.setenv("FAILOVER_BACKENDS", "pi=https://pi.t.ts.net,pc=https://pc.t.ts.net")
    s.config["webapp"]["public_origin"] = ""
    # failover opts in: the server profile turns it off (Kwil is cloud-only)
    s.config["failover"].update(enabled=True, role="cloud", app_origin="https://kwil.example")
    assert s.connect.enabled(None) is True
    assert s.connect.redirect_uri == "https://kwil.example/google/callback"
    assert s.connect._redirect("a-member") == "https://kwil.example/google/callback"
    r = owner_client(s).post("/api/google/connect", json={})
    from urllib.parse import parse_qs, urlparse
    q = parse_qs(urlparse(r.json()["url"]).query)
    assert q["redirect_uri"] == ["https://kwil.example/google/callback"]
    s.config["failover"].update(role="", app_origin="")
    assert s.connect.enabled(None) is False


def test_without_failover_the_cloud_needs_public_origin_for_google(server, monkeypatch):
    """Cloud-only (2026-10-31): the server profile's [failover] enabled =
    false means APP_ORIGIN no longer gives the cloud its address; only
    PUBLIC_ORIGIN does (Render: https://heykwil.com)."""
    s = server
    monkeypatch.setattr("kwil.failover.peers._warned_disabled", True, raising=False)
    s.config["failover"].update(enabled=False, role="cloud", app_origin="https://kwil.example")
    s.config["webapp"]["public_origin"] = ""
    assert s.connect.enabled(None) is False  # never a stale APP_ORIGIN
    s.config["webapp"]["public_origin"] = "https://heykwil.com"
    assert s.connect.enabled(None) is True
    assert s.connect.redirect_uri == "https://heykwil.com/google/callback"


def test_owner_disconnect_falls_back_to_a_refused_env_token_and_says_so(
        server, monkeypatch):
    """GOOGLE_TOKEN_JSON revoked -> Reconnect (a file) -> Disconnect: the
    status is back on the env token, which Google still refuses."""
    from google.oauth2.credentials import Credentials

    s = server
    kyle = owner_client(s)
    owner_stale_token(monkeypatch)
    monkeypatch.setattr(Credentials, "refresh", _refused)
    use_calendar(s.assistant.tools)
    assert kyle.get("/api/google/status").json()["needs_reconnect"] is True
    owner_connects(s, kyle)  # the file wins: nothing refused it
    assert kyle.get("/api/google/status").json()["needs_reconnect"] is False
    assert kyle.post("/api/google/disconnect", json={}).status_code == 200
    st = kyle.get("/api/google/status").json()
    assert st["connected"] is True and st["needs_reconnect"] is True  # the env's again
    assert needs_reconnect(OWNER, "env") and not needs_reconnect(OWNER, "file")


# --- the status: a family member's own token ------------------------------------------

def test_member_status_says_reconnect_and_reconnecting_clears_it(g, monkeypatch):
    from google.oauth2.credentials import Credentials

    from tests.test_family_google import token_of

    mom, mom_id = g.member("Mom", "zh")
    dad, dad_id = g.member("Dad", "en")
    connected(g, mom, mom_id, "mom")
    connected(g, dad, dad_id, "dad")
    g.fam.google_token(mom_id).write_text(json.dumps(stale(token_of("mom"))))
    monkeypatch.setattr(Credentials, "refresh", _refused)
    result, is_error = use_calendar(g.fam.context(mom_id).registry)
    assert is_error
    st = mom.get("/api/google/status").json()
    assert st["connected"] is True and st["needs_reconnect"] is True
    assert st["enabled"] is True  # the Reconnect button
    assert dad.get("/api/google/status").json()["needs_reconnect"] is False
    assert needs_reconnect(mom_id) and not needs_reconnect(OWNER)
    connected(g, mom, mom_id, "mom")  # Reconnect
    assert mom.get("/api/google/status").json()["needs_reconnect"] is False
    # and again, then Disconnect
    google_auth.mark_refresh_failed(mom_id)
    assert mom.post("/api/google/disconnect", json={}).status_code == 200
    assert not needs_reconnect(mom_id)
    assert mom.get("/api/google/status").json()["needs_reconnect"] is False


# --- doctor -------------------------------------------------------------------------

def test_verify_sign_in_says_expired_or_revoked_in_plain_words(tmp_path, monkeypatch):
    from google.oauth2.credentials import Credentials

    token = tmp_path / "token.json"
    token.write_text(json.dumps(ENV_TOKEN))  # valid by its expiry: still asks Google
    seen = []

    class FakeRequest:  # google.auth.transport.requests.Request, offline
        def __call__(self, *args, **kwargs):
            seen.append(kwargs)

    monkeypatch.setattr("google.auth.transport.requests.Request", FakeRequest)

    def asks(self, request):
        request(url="https://oauth2.googleapis.com/token", method="POST")
        _refused(self, request)

    monkeypatch.setattr(Credentials, "refresh", asks)
    problem = google_auth.verify_sign_in("", token, environ={})
    assert "invalid_grant" in problem and "expired or was revoked" in problem
    assert "sign in to Google again" in problem
    assert seen == [{"url": "https://oauth2.googleapis.com/token", "method": "POST",
                     "timeout": google_auth.CHECK_TIMEOUT}]  # bounded
    assert "ya29" not in problem and "secret" not in problem
    monkeypatch.setattr(Credentials, "refresh", _refreshed)
    assert google_auth.verify_sign_in("", token, environ={}) is None

    def offline(self, request):
        from google.auth.exceptions import TransportError

        raise TransportError("connection refused to oauth2.googleapis.com")

    monkeypatch.setattr(Credentials, "refresh", offline)
    assert google_auth.verify_sign_in("", token, environ={}) == \
        "couldn't check with Google (TransportError)"


def test_doctor_reports_an_expired_sign_in_on_the_server(server):
    s = server
    asked = []

    def verify(credentials, token, environ, in_app):
        asked.append((credentials, token, environ.get("GOOGLE_TOKEN_JSON") is not None,
                      in_app))
        return "Google refused it (invalid_grant): the sign-in expired or was revoked"

    checks = doctor.check_google_env(s.config, verify=verify)
    assert [c.name for c in checks] == ["GOOGLE_TOKEN_JSON", "Google sign-in"]
    assert checks[1].ok is False and checks[1].required is False
    assert "expired or was revoked" in checks[1].detail
    assert asked == [(s.config["calendar"]["credentials"], s.config["calendar"]["token"],
                      True, False)]  # the env's token, Kyle's advice
    # connected in the app: that token is the one checked
    owner_connects(s, owner_client(s))
    checks = doctor.check_google_env(s.config, verify=lambda *a, **k: None)
    assert [c.name for c in checks] == ["Google account", "Google sign-in"]
    assert checks[1].ok and "works" in checks[1].detail
    # without verify (tests, other callers): no line, no network
    assert [c.name for c in doctor.check_google_env(s.config)] == ["Google account"]


def test_doctor_checks_a_pc_token_file_only_when_asked(tmp_path):
    from kwil.config import load_config

    config = load_config(None, None)
    config["calendar"].update(credentials=str(tmp_path / "credentials.json"),
                              token=str(tmp_path / "token.json"))
    calls = []

    def verify(*args, **kwargs):
        calls.append(args)
        return None

    names = [c.name for c in doctor.check_google_files(config, verify)]
    assert "Google sign-in" not in names and calls == []  # no token: nothing to ask
    (tmp_path / "token.json").write_text(json.dumps(ENV_TOKEN))
    checks = doctor.check_google_files(config, verify, environ={})
    assert checks[-1].name == "Google sign-in" and checks[-1].ok
    assert calls == [(config["calendar"]["credentials"], config["calendar"]["token"])]
    assert "Google sign-in" not in [c.name for c in doctor.check_google_files(config)]
