"""Spotify by voice (assistant/tools/spotify.py, assistant/spotify_web.py).

Offline: the HTTP layer is a fake that answers like accounts.spotify.com
and api.spotify.com (their real JSON shapes, including the error object
{"error": {"status", "message", "reason"}} and null entries in search
results). Nothing here reaches Spotify."""

import json
import logging
import os
import sys
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from assistant import cli, i18n
from assistant.config import load_config, redact
from assistant.tools import ToolRegistry
from assistant.tools import spotify as sp

CID = "0123456789abcdef0123456789abcdef"
ORIGIN = "https://pc.example.ts.net"
NOW = 1_800_000_000


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self.body = body
        self.content = b"" if body is None else json.dumps(body).encode()

    def json(self):
        if self.body is None:
            raise ValueError("no body")
        return self.body


def api_error(status, message, reason=None):
    err = {"status": status, "message": message}
    if reason:
        err["reason"] = reason
    return Resp(status, {"error": err})


class FakeHTTP:
    """requests.Session.request stand-in. `routes[(METHOD, path)]` is a
    Resp, a list of them (one per call; the last repeats) or a function of
    the call."""

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.calls = []

    def request(self, method, url, params=None, json=None, data=None, headers=None,
                timeout=None):
        path = url.replace(sp.API, "") if url.startswith(sp.API) else url
        call = SimpleNamespace(method=method, path=path, params=params or {}, json=json,
                               data=data, headers=headers or {}, timeout=timeout)
        self.calls.append(call)
        route = self.routes.get((method, path))
        if route is None:
            return Resp(204)
        if callable(route):
            return route(call)
        if isinstance(route, list):
            return route.pop(0) if len(route) > 1 else route[0]
        return route

    def to(self, method, path):
        return [c for c in self.calls if c.method == method and c.path == path]


def token(**over):
    t = {"access_token": "BQ-access-secret", "refresh_token": "AQ-refresh-secret",
         "expires_at": NOW + 3600, "scope": " ".join(sp.SCOPES), "token_type": "Bearer"}
    t.update(over)
    return t


@pytest.fixture
def token_file(tmp_path):
    path = tmp_path / "data" / "spotify_token.json"
    sp.save_token(path, token())
    return path


def make_player(token_file, routes=None, local="KYLE-PC"):
    http = FakeHTTP(routes)
    client = sp.SpotifyClient(token_file, cid=CID, http=http, clock=lambda: NOW)
    return sp.SpotifyPlayer(client, local_device=local), http


DEVICES = {"devices": [
    {"id": "pc1", "is_active": False, "is_private_session": False, "is_restricted": False,
     "name": "KYLE-PC", "type": "Computer", "volume_percent": 60, "supports_volume": True},
    {"id": "ph1", "is_active": False, "is_private_session": False, "is_restricted": False,
     "name": "Kyle's iPhone", "type": "Smartphone", "volume_percent": 100,
     "supports_volume": False}]}

PLAYLISTS = {"href": "x", "limit": 50, "offset": 0, "total": 2, "next": None, "items": [
    {"id": "pl1", "name": "Study", "uri": "spotify:playlist:pl1", "type": "playlist",
     "owner": {"id": "kyle", "display_name": "Kyle", "type": "user"},
     "tracks": {"total": 40}},
    {"id": "pl2", "name": "Gym Hits", "uri": "spotify:playlist:pl2", "type": "playlist",
     "owner": {"id": "spotify", "display_name": "Spotify", "type": "user"},
     "tracks": {"total": 50}}]}

TRACK = {"id": "t1", "name": "Blinding Lights", "uri": "spotify:track:t1", "type": "track",
         "artists": [{"id": "a1", "name": "The Weeknd", "type": "artist"}],
         "album": {"id": "al1", "name": "After Hours", "type": "album"}}
SEARCH = {
    "tracks": {"items": [{**TRACK, "name": "Lofi Song", "uri": "spotify:track:t9"}]},
    "playlists": {"items": [None, {"id": "pl9", "name": "lofi beats", "uri":
                                   "spotify:playlist:pl9", "type": "playlist",
                                   "owner": {"id": "spotify", "display_name": "Spotify"}}]},
    "artists": {"items": []}, "albums": {"items": []}}
PLAYER = {"device": {"id": "pc1", "is_active": True, "name": "KYLE-PC", "type": "Computer",
                     "volume_percent": 40, "supports_volume": True},
          "shuffle_state": False, "repeat_state": "off", "timestamp": 1, "progress_ms": 1000,
          "is_playing": True, "currently_playing_type": "track", "item": TRACK,
          "context": {"type": "playlist", "uri": "spotify:playlist:pl1"}}


# --- sign-in -----------------------------------------------------------------------

def test_pkce_challenge_is_unpadded_base64url_sha256():
    # SHA-256("abc") = ba7816bf...; S256 is its base64url form without "="
    assert sp.challenge("abc") == "ungWv48Bz-pBQUDeXa4iI7ADYaOWF3qctBD_YfIAFa0"
    v = sp.new_verifier()
    assert 43 <= len(v) <= 128 and v != sp.new_verifier()


def test_authorize_url_asks_for_the_minimum():
    url = sp.authorize_url(CID, "http://127.0.0.1:8888/callback", "st", "verifier")
    assert url.startswith("https://accounts.spotify.com/authorize?")
    q = parse_qs(urlparse(url).query)
    assert set(q["scope"][0].split()) == {"user-read-playback-state",
                                          "user-modify-playback-state",
                                          "playlist-read-private"}
    assert len(q["scope"][0].split()) == 3
    assert not any("modify" in s and "playlist" in s for s in q["scope"][0].split())
    assert q["code_challenge_method"] == ["S256"] and q["state"] == ["st"]
    assert q["code_challenge"] == [sp.challenge("verifier")]
    assert "verifier" not in url and "secret" not in url


def test_token_file_is_private(token_file):
    assert json.loads(token_file.read_text())["refresh_token"] == "AQ-refresh-secret"
    if os.name != "nt":
        assert token_file.stat().st_mode & 0o777 == 0o600


def test_cli_sign_in_checks_state_and_saves(tmp_path, monkeypatch):
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", CID)
    config = load_config(None, None)
    config["spotify"]["token"] = str(tmp_path / "t.json")
    shown = []
    http = FakeHTTP({("POST", sp.TOKEN_URL): Resp(200, {
        "access_token": "BQ-new", "token_type": "Bearer", "expires_in": 3600,
        "refresh_token": "AQ-new", "scope": " ".join(sp.SCOPES)})})

    def paste(_prompt):
        q = parse_qs(urlparse(shown[0].split("\n\n")[1]).query)
        return f"http://127.0.0.1:8888/callback?code=AUTHCODE&state={q['state'][0]}"
    sp.sign_in(config, input_fn=paste, output_fn=shown.append, http=http, open_browser=False)
    form = http.calls[0].data
    assert form["grant_type"] == "authorization_code" and form["code"] == "AUTHCODE"
    assert form["client_id"] == CID and "client_secret" not in form
    q = parse_qs(urlparse(shown[0].split("\n\n")[1]).query)
    assert sp.challenge(form["code_verifier"]) == q["code_challenge"][0]
    assert sp.load_token(tmp_path / "t.json")["refresh_token"] == "AQ-new"
    with pytest.raises(SystemExit):  # another sign-in's address
        sp.sign_in(config, input_fn=lambda _: "http://127.0.0.1:8888/callback?code=x&state=old",
                   output_fn=lambda _: None, http=http, open_browser=False)


def test_expired_token_is_refreshed_and_saved(token_file):
    sp.save_token(token_file, token(expires_at=NOW - 10))
    http = FakeHTTP({("POST", sp.TOKEN_URL): Resp(200, {
        "access_token": "BQ-fresh", "token_type": "Bearer", "expires_in": 3600,
        "refresh_token": "AQ-rotated", "scope": "user-read-playback-state"})})
    client = sp.SpotifyClient(token_file, cid=CID, http=http, clock=lambda: NOW)
    client.request("GET", "/me/player")
    refresh = http.to("POST", sp.TOKEN_URL)[0].data
    assert refresh == {"grant_type": "refresh_token", "client_id": CID,
                       "refresh_token": "AQ-refresh-secret"}
    assert http.to("GET", "/me/player")[0].headers["Authorization"] == "Bearer BQ-fresh"
    saved = sp.load_token(token_file)
    assert saved["refresh_token"] == "AQ-rotated" and saved["expires_at"] == NOW + 3600
    if os.name != "nt":
        assert token_file.stat().st_mode & 0o777 == 0o600


def test_refresh_without_a_new_refresh_token_keeps_the_old_one(token_file):
    sp.save_token(token_file, token(expires_at=NOW - 10))
    http = FakeHTTP({("POST", sp.TOKEN_URL): Resp(200, {
        "access_token": "BQ-fresh", "token_type": "Bearer", "expires_in": 3600})})
    sp.SpotifyClient(token_file, cid=CID, http=http, clock=lambda: NOW).access_token()
    assert sp.load_token(token_file)["refresh_token"] == "AQ-refresh-secret"


def test_a_401_refreshes_once_and_retries(token_file):
    http = FakeHTTP({("GET", "/me/player"): [api_error(401, "The access token expired"),
                                              Resp(200, PLAYER)],
                     ("POST", sp.TOKEN_URL): Resp(200, {"access_token": "BQ-fresh",
                                                        "expires_in": 3600})})
    client = sp.SpotifyClient(token_file, cid=CID, http=http, clock=lambda: NOW)
    assert client.request("GET", "/me/player")["is_playing"] is True
    assert len(http.to("GET", "/me/player")) == 2
    assert len(http.to("POST", sp.TOKEN_URL)) == 1


def test_a_token_another_process_refreshed_is_used(token_file):
    http = FakeHTTP()
    client = sp.SpotifyClient(token_file, cid=CID, http=http, clock=lambda: NOW)
    client.token = token(access_token="BQ-old", expires_at=NOW - 5)
    sp.save_token(token_file, token(access_token="BQ-other-process"))
    client.request("GET", "/me/player")
    assert not http.to("POST", sp.TOKEN_URL)
    assert http.calls[0].headers["Authorization"] == "Bearer BQ-other-process"


def test_revoked_sign_in_reads_nicely(token_file):
    sp.save_token(token_file, token(expires_at=NOW - 10))
    player, http = make_player(token_file, {("POST", sp.TOKEN_URL): Resp(400, {
        "error": "invalid_grant", "error_description": "Refresh token revoked"})})
    out = player.pause()
    assert out["reason"] == "signin" and "Connect Spotify again" in out["message"]


# --- the tools ---------------------------------------------------------------------

def test_play_my_study_playlist_uses_my_playlists_first(token_file):
    player, http = make_player(token_file, {("GET", "/me/playlists"): Resp(200, PLAYLISTS),
                                            ("GET", "/me"): Resp(200, {"id": "kyle"})})
    out = player.play("my study playlist")
    put = http.to("PUT", "/me/player/play")[0]
    assert put.json == {"context_uri": "spotify:playlist:pl1"}
    assert not http.to("GET", "/search")
    assert out["status"] == "playing" and out["name"] == "Study"
    assert out["from"] == "your playlists"
    assert "untrusted_content" not in out  # Kyle's own playlist name
    player.play("gym hits")  # followed, made by someone else: others' text
    assert http.to("PUT", "/me/player/play")[-1].json == {"context_uri": "spotify:playlist:pl2"}


def test_followed_playlist_by_others_is_marked_untrusted(token_file):
    player, _ = make_player(token_file, {("GET", "/me/playlists"): Resp(200, PLAYLISTS),
                                         ("GET", "/me"): Resp(200, {"id": "kyle"})})
    assert player.play("gym hits")["untrusted_content"] is True


def test_play_some_lofi_searches_the_catalog(token_file):
    player, http = make_player(token_file, {("GET", "/me/playlists"): Resp(200, PLAYLISTS),
                                            ("GET", "/search"): Resp(200, SEARCH)})
    out = player.play("some lo-fi")
    search = http.to("GET", "/search")[0]
    assert search.params["q"] == "some lo-fi" and search.params["limit"] == 5
    assert set(search.params["type"].split(",")) == {"track", "playlist", "artist", "album"}
    # a mood: the playlist (the null entry Spotify sends is skipped)
    assert http.to("PUT", "/me/player/play")[0].json == {"context_uri": "spotify:playlist:pl9"}
    assert out["kind"] == "playlist" and out["from"] == "Spotify"
    assert out["untrusted_content"] is True
    assert '"untrusted_content": true' in json.dumps(out)  # chat.has_marked_results


def test_play_a_song_by_name_plays_that_track(token_file):
    found = {"tracks": {"items": [TRACK]}, "playlists": {"items": []},
             "artists": {"items": []}, "albums": {"items": []}}
    player, http = make_player(token_file, {("GET", "/me/playlists"): Resp(200, PLAYLISTS),
                                            ("GET", "/search"): Resp(200, found)})
    out = player.play("Blinding Lights")
    assert http.to("PUT", "/me/player/play")[0].json == {"uris": ["spotify:track:t1"]}
    assert out["kind"] == "track" and out["by"] == "The Weeknd"


def test_nothing_found(token_file):
    empty = {t: {"items": []} for t in ("tracks", "playlists", "artists", "albums")}
    player, http = make_player(token_file, {("GET", "/me/playlists"): Resp(200, PLAYLISTS),
                                            ("GET", "/search"): Resp(200, empty)})
    out = player.play("zzqx")
    assert out["status"] == "not_found" and "zzqx" in out["message"]
    assert not http.to("PUT", "/me/player/play")


def test_play_on_my_phone_picks_the_device(token_file):
    player, http = make_player(token_file, {("GET", "/me/playlists"): Resp(200, PLAYLISTS),
                                            ("GET", "/me/player/devices"): Resp(200, DEVICES)})
    out = player.play("study", device="my phone")
    assert http.to("PUT", "/me/player/play")[0].params == {"device_id": "ph1"}
    assert out["device"] == "Kyle's iPhone"
    out = player.play("study", device="the toaster")
    assert out["reason"] == "unknown_device" and "toaster" in out["message"]


@pytest.mark.parametrize("tool,method,path,status", [
    ("pause", "PUT", "/me/player/pause", "paused"),
    ("resume", "PUT", "/me/player/play", "playing"),
    ("next", "POST", "/me/player/next", "skipped"),
    ("previous", "POST", "/me/player/previous", "went_back")])
def test_simple_controls(token_file, tool, method, path, status):
    player, http = make_player(token_file)
    assert getattr(player, tool)() == {"status": status}
    assert [(c.method, c.path) for c in http.calls] == [(method, path)]
    assert http.calls[0].headers["Authorization"] == "Bearer BQ-access-secret"
    assert "BQ-access" not in json.dumps(http.calls[0].params)  # never in the URL


def test_play_with_no_query_resumes(token_file):
    player, http = make_player(token_file)
    assert player.play()["resumed"] is True
    assert http.to("PUT", "/me/player/play")[0].json is None


def test_already_paused_is_fine(token_file):
    player, _ = make_player(token_file, {("PUT", "/me/player/pause"): api_error(
        403, "Player command failed: Restriction violated", "ALREADY_PAUSED")})
    assert player.pause() == {"status": "paused"}


def test_turn_it_up_and_down(token_file):
    player, http = make_player(token_file, {("GET", "/me/player"): Resp(200, PLAYER)})
    out = player.volume()
    assert http.to("PUT", "/me/player/volume")[0].params == {"volume_percent": 55,
                                                              "device_id": "pc1"}
    assert out == {"status": "volume_set", "volume": 55, "was": 40, "device": "KYLE-PC",
                   "untrusted_content": True}
    assert player.volume(change=-60)["volume"] == 0
    assert player.volume(level=150)["volume"] == 100


def test_volume_on_a_phone_reads_nicely(token_file):
    phone = {**PLAYER, "device": {**DEVICES["devices"][1], "is_active": True}}
    player, http = make_player(token_file, {("GET", "/me/player"): Resp(200, phone)})
    out = player.volume(level=30)
    assert out["reason"] == "no_volume" and "volume" in out["message"]
    assert not http.to("PUT", "/me/player/volume")


def test_volume_disallowed_by_spotify(token_file):
    player, _ = make_player(token_file, {
        ("GET", "/me/player"): Resp(200, PLAYER),
        ("PUT", "/me/player/volume"): api_error(403, "Player command failed: Cannot control "
                                                "device volume", "VOLUME_CONTROL_DISALLOW")})
    assert player.volume(level=30)["reason"] == "no_volume"


def test_whats_playing(token_file):
    player, http = make_player(token_file, {("GET", "/me/player"): Resp(200, PLAYER)})
    out = player.now_playing()
    assert http.calls[0].params == {"additional_types": "episode"}
    assert out["status"] == "playing" and out["name"] == "Blinding Lights"
    assert out["by"] == "The Weeknd" and out["album"] == "After Hours"
    assert out["device"] == "KYLE-PC" and out["volume"] == 40
    assert out["untrusted_content"] is True


def test_nothing_playing(token_file):
    player, _ = make_player(token_file, {("GET", "/me/player"): Resp(204)})
    out = player.now_playing()
    assert out["status"] == "nothing_playing"
    assert out["message"] == "Nothing is playing on Spotify right now."


def test_devices_and_transfer(token_file):
    player, http = make_player(token_file, {("GET", "/me/player/devices"): Resp(200, DEVICES)})
    out = player.devices()
    assert out["devices"][0] == {"name": "KYLE-PC", "type": "Computer", "active": False,
                                 "this_computer": True, "can_control": True}
    assert "id" not in out["devices"][0]
    out = player.transfer("phone")
    assert http.to("PUT", "/me/player")[0].json == {"device_ids": ["ph1"], "play": True}
    assert out == {"status": "moved", "device": "Kyle's iPhone", "untrusted_content": True}
    player.transfer("this computer")
    assert http.to("PUT", "/me/player")[-1].json["device_ids"] == ["pc1"]


# --- errors read nicely -------------------------------------------------------------

def test_no_premium(token_file):
    player, _ = make_player(token_file, {("PUT", "/me/player/pause"): api_error(
        403, "Player command failed: Premium required", "PREMIUM_REQUIRED")})
    out = player.pause()
    assert out == {"status": "error", "reason": "premium",
                   "message": "Spotify only lets apps control playback with a Premium account."}


def test_premium_without_a_reason_field(token_file):
    player, _ = make_player(token_file, {("POST", "/me/player/next"): api_error(
        403, "Player command failed: Premium required")})
    assert player.next()["reason"] == "premium"


NO_DEVICE = api_error(404, "Player command failed: No active device found", "NO_ACTIVE_DEVICE")


def test_no_active_device_and_none_open(token_file):
    player, _ = make_player(token_file, {("PUT", "/me/player/pause"): NO_DEVICE,
                                         ("GET", "/me/player/devices"): Resp(200, {"devices": []})})
    out = player.pause()
    assert out["reason"] == "no_device" and "Open Spotify" in out["message"]


def test_no_active_device_uses_this_computer(token_file):
    player, http = make_player(token_file, {
        ("PUT", "/me/player/play"): [NO_DEVICE, Resp(204)],
        ("GET", "/me/player/devices"): Resp(200, DEVICES)})
    out = player.resume()
    plays = http.to("PUT", "/me/player/play")
    assert len(plays) == 2 and plays[1].params == {"device_id": "pc1"}
    assert out == {"status": "playing", "device": "KYLE-PC", "untrusted_content": True}


def test_no_active_device_asks_which(token_file):
    player, http = make_player(token_file, {
        ("PUT", "/me/player/play"): NO_DEVICE,
        ("GET", "/me/player/devices"): Resp(200, DEVICES)}, local="OTHER-PC")
    out = player.resume()
    assert out["reason"] == "which_device"
    assert out["devices"] == ["KYLE-PC", "Kyle's iPhone"]
    assert "KYLE-PC, Kyle's iPhone" in out["message"]
    assert len(http.to("PUT", "/me/player/play")) == 1


def test_a_failed_skip_is_never_repeated(token_file):
    player, http = make_player(token_file, {("POST", "/me/player/next"): api_error(
        502, "Bad gateway")})
    assert player.next()["reason"] == "error"
    assert len(http.to("POST", "/me/player/next")) == 1


def test_rate_limit_and_network_errors(token_file):
    player, _ = make_player(token_file, {("POST", "/me/player/next"): api_error(
        429, "API rate limit exceeded")})
    assert player.next()["message"] == "Spotify is busy right now. Try again in a moment."

    def down(call):
        raise ConnectionError("no route to host")
    player, _ = make_player(token_file, {("PUT", "/me/player/pause"): down})
    assert player.pause()["reason"] == "unreachable"


def test_chinese_messages_and_device_words(token_file):
    i18n.set_language("zh")
    player, http = make_player(token_file, {
        ("PUT", "/me/player/pause"): api_error(403, "Premium required", "PREMIUM_REQUIRED"),
        ("GET", "/me/player/devices"): Resp(200, DEVICES)})
    assert player.pause()["message"] == "Spotify 只允许高级会员（Premium）账户用应用控制播放。"
    assert player.transfer("我的手机")["device"] == "Kyle's iPhone"
    assert player.transfer("电脑")["device"] == "KYLE-PC"
    assert "没有找到叫“冰箱”" in player.transfer("冰箱")["message"]
    for key in [k for k in i18n.EN if k.startswith("spotify_")]:
        assert key in i18n.ZH, key
    assert sp._norm("我的学习歌单") == "学习"


# --- registration -------------------------------------------------------------------

def spotify_config(tmp_path, token_file=None, enabled=True):
    config = load_config(None, None)
    for section in ("calendar", "notion", "web", "devteam"):
        config[section]["enabled"] = False
    config["spotify"].update(enabled=enabled,
                             token=str(token_file or tmp_path / "none.json"))
    return config


def test_off_by_default():
    config = load_config(None, None)
    assert config["spotify"]["enabled"] is False
    assert config["spotify"]["pause_while_listening"] is True


def test_tools_absent_when_disabled_or_not_connected(tmp_path, token_file, monkeypatch):
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", CID)
    said = []
    assert not cli.build_tools(spotify_config(tmp_path, token_file, enabled=False),
                               said.append).tools
    assert not cli.build_tools(spotify_config(tmp_path), said.append).tools
    assert any("Spotify tools off" in s for s in said)
    monkeypatch.delenv("SPOTIFY_CLIENT_ID")
    assert not cli.build_tools(spotify_config(tmp_path, token_file), said.append).tools


def test_tools_present_when_enabled_and_connected(tmp_path, token_file, monkeypatch):
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", CID)
    tools = cli.build_tools(spotify_config(tmp_path, token_file), lambda _: None)
    assert set(tools.tools) == {"spotify_play", "spotify_pause", "spotify_resume",
                                "spotify_next", "spotify_previous", "spotify_volume",
                                "spotify_now_playing", "spotify_devices",
                                "spotify_transfer"}
    # playback control is harmless: no confirmation; nothing private
    for tool in tools.tools.values():
        assert tool.confirm_prompt is None and tool.confirm_if_untrusted is None
    assert isinstance(sp.player_of(tools), sp.SpotifyPlayer)
    assert sp.player_of(ToolRegistry()) is None


def test_no_tool_changes_playlists_or_the_library():
    import inspect

    src = inspect.getsource(sp)
    for forbidden in ("playlist-modify", "user-library-modify", "/tracks\"",
                      "/me/tracks", "/following", "\"POST\", \"/playlists"):
        assert forbidden not in src


def test_dispatch_logs_no_query_or_names_at_info(tmp_path, token_file, monkeypatch, caplog):
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", CID)
    http = FakeHTTP({("GET", "/me/playlists"): Resp(200, PLAYLISTS),
                     ("GET", "/search"): Resp(200, SEARCH),
                     ("GET", "/me/player"): Resp(200, PLAYER)})
    tools = ToolRegistry()
    cli.register_spotify(tools, spotify_config(tmp_path, token_file), http=http)
    caplog.set_level(logging.DEBUG)
    tools.dispatch("spotify_play", {"query": "secret sad songs"})
    tools.dispatch("spotify_now_playing", {})
    info = [r.getMessage() for r in caplog.records if r.levelno >= logging.INFO]
    assert any("Spotify: playing a playlist from search" in m for m in info)
    for text in ("secret sad songs", "lofi beats", "Blinding Lights", "BQ-access",
                 "AQ-refresh"):
        assert not any(text in m for m in info), text
    assert any("secret sad songs" in r.getMessage() for r in caplog.records
               if r.levelno == logging.DEBUG)  # tool input: DEBUG only


def test_tokens_are_redacted_in_logs():
    line = "POST token: {'access_token': 'BQ-abc', 'refresh_token': 'AQ-def'} Bearer BQ-xyz"
    out = redact(line)
    assert "BQ-abc" not in out and "AQ-def" not in out and "BQ-xyz" not in out
    assert "AUTHCODE" not in redact("GET /spotify/callback?state=s&code=AUTHCODE")


# --- pausing music while Kyle talks (wake mode) ---------------------------------------

def test_pause_while_listening_and_resume_after(token_file):
    player, http = make_player(token_file, {("GET", "/me/player"): Resp(200, PLAYER)})
    player.pause_for_listening()
    player._pausing.join(5)
    assert http.to("PUT", "/me/player/pause")[0].params == {"device_id": "pc1"}
    assert player.now_playing()["paused_while_you_talk"] is True
    assert player.resume_after_listening() is True
    assert http.to("PUT", "/me/player/play")[0].params == {"device_id": "pc1"}
    assert player.resume_after_listening() is False  # only once


def test_no_resume_after_kyle_paused_it_himself(token_file):
    player, http = make_player(token_file, {("GET", "/me/player"): Resp(200, PLAYER)})
    player.pause_for_listening()
    player._pausing.join(5)
    player.pause()  # "Hey Claude, pause"
    assert player.resume_after_listening() is False
    assert not http.to("PUT", "/me/player/play")


def test_music_elsewhere_or_stopped_is_left_alone(token_file):
    phone = {**PLAYER, "device": {**PLAYER["device"], "id": "ph1", "name": "Kyle's iPhone"}}
    stopped = {**PLAYER, "is_playing": False}
    for state in (phone, stopped):
        player, http = make_player(token_file, {("GET", "/me/player"): Resp(200, state)})
        player.pause_for_listening()
        player._pausing.join(5)
        assert not http.to("PUT", "/me/player/pause")
        assert player.resume_after_listening() is False


def test_pause_failure_never_breaks_listening(token_file, caplog):
    def boom(call):
        raise ConnectionError("down")
    player, _ = make_player(token_file, {("GET", "/me/player"): boom})
    player.pause_for_listening()
    player._pausing.join(5)
    assert player.resume_after_listening() is False


def test_local_device_defaults_to_hostname(token_file):
    import socket

    player, _ = make_player(token_file, local="")
    assert player.local_device == socket.gethostname()


class OneWake:
    """A listener that hears the wake word once with a command, then the
    file ends."""

    def __init__(self, on_wake):
        self.on_wake = on_wake
        self.done = False

    def listen(self, source):
        if self.done:
            return False, None
        self.done = True
        self.on_wake()
        return True, b"audio"


def test_run_wake_resumes_when_back_to_waiting(fake_client, token_file):
    from assistant.chat import ClaudeChat
    from tests.conftest import make_response, text_block

    events = []
    fake_client.queue.append(make_response(text_block("Sure.")))
    chat = ClaudeChat(client=fake_client, model="m")
    cli.run_wake(chat, SimpleNamespace(speak=lambda t: events.append("speak")),
                 SimpleNamespace(transcribe=lambda r: "what time is it"),
                 OneWake(lambda: events.append("wake")), None,
                 output_fn=lambda _: None, follow_up_seconds=0,
                 on_idle=lambda: events.append("idle"))
    assert events[:3] == ["wake", "speak", "idle"]


def test_wake_components_pause_music_first(monkeypatch, token_file):
    calls = []
    monkeypatch.setattr(cli.audio, "play", lambda *_: calls.append("chime"))
    music = SimpleNamespace(pause_for_listening=lambda: calls.append("pause"))
    both = cli.chain(music.pause_for_listening, lambda: cli.audio.play(None))
    both()
    assert calls == ["pause", "chime"]
    assert cli.chain(music.pause_for_listening) is music.pause_for_listening


def test_music_to_pause_follows_the_setting(tmp_path, token_file, monkeypatch):
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", CID)
    config = spotify_config(tmp_path, token_file)
    tools = cli.build_tools(config, lambda _: None)
    assert isinstance(cli.music_to_pause(config, tools), sp.SpotifyPlayer)
    config["spotify"]["pause_while_listening"] = False
    assert cli.music_to_pause(config, tools) is None


def test_a_pause_still_on_its_way_is_not_resumed_after_kyle_pauses(token_file):
    import threading

    gate = threading.Event()

    def slow_state(call):
        gate.wait(5)
        return Resp(200, PLAYER)
    player, http = make_player(token_file, {("GET", "/me/player"): slow_state})
    player.pause_for_listening()
    threading.Timer(0.2, gate.set).start()
    player.pause()  # waits for the wake-word pause, then forgets it
    assert player.resume_after_listening() is False
    assert not http.to("PUT", "/me/player/play")


# --- round 2 ----------------------------------------------------------------------

class RotatingTokens:
    """Spotify's token endpoint with rotating refresh tokens: each refresh
    token works once. `before` runs just before the first refresh is
    answered (the other process winning the race)."""

    def __init__(self, before=None):
        self.valid, self.n, self.before = {"AQ-refresh-secret"}, 0, before

    def __call__(self, call):
        if self.before:
            before, self.before = self.before, None
            before()
        rt = call.data["refresh_token"]
        if rt not in self.valid:
            return Resp(400, {"error": "invalid_grant", "error_description": "Invalid refresh token"})
        self.valid.discard(rt)
        self.n += 1
        self.valid.add(f"AQ-{self.n}")
        return Resp(200, {"access_token": f"BQ-{self.n}", "refresh_token": f"AQ-{self.n}",
                          "expires_in": 3600})


def test_refresh_race_with_another_process_uses_its_token(token_file):
    sp.save_token(token_file, token(expires_at=NOW - 10))
    other = sp.SpotifyClient(token_file, cid=CID, http=None, clock=lambda: NOW)
    endpoint = RotatingTokens(before=lambda: other.access_token())
    other._http = FakeHTTP({("POST", sp.TOKEN_URL): endpoint})
    http = FakeHTTP({("POST", sp.TOKEN_URL): endpoint})
    mine = sp.SpotifyClient(token_file, cid=CID, http=http, clock=lambda: NOW)
    assert mine.access_token() == "BQ-1"  # the other process's, not "sign-in expired"
    assert sp.load_token(token_file)["refresh_token"] == "AQ-1"


def test_refresh_race_retries_once_with_the_rotated_refresh_token(token_file):
    """The other process rotated the refresh token, but its access token
    has expired too: refresh once more with the new refresh token."""
    sp.save_token(token_file, token(expires_at=NOW - 10))
    endpoint = RotatingTokens()

    def other_wins():
        endpoint.valid = {"AQ-rotated"}
        sp.save_token(token_file, token(access_token="BQ-stale", refresh_token="AQ-rotated",
                                        expires_at=NOW - 5))
    endpoint.before = other_wins
    http = FakeHTTP({("POST", sp.TOKEN_URL): endpoint})
    client = sp.SpotifyClient(token_file, cid=CID, http=http, clock=lambda: NOW)
    assert client.access_token() == "BQ-1"
    sent = [c.data["refresh_token"] for c in http.to("POST", sp.TOKEN_URL)]
    assert sent == ["AQ-refresh-secret", "AQ-rotated"]
    assert sp.load_token(token_file)["refresh_token"] == "AQ-1"


def test_a_really_revoked_token_still_says_sign_in(token_file):
    sp.save_token(token_file, token(expires_at=NOW - 10))
    http = FakeHTTP({("POST", sp.TOKEN_URL): Resp(400, {"error": "invalid_grant"})})
    client = sp.SpotifyClient(token_file, cid=CID, http=http, clock=lambda: NOW)
    with pytest.raises(sp.SpotifyError) as e:
        client.access_token()
    assert e.value.reason == "signin" and len(http.to("POST", sp.TOKEN_URL)) == 1


def test_token_file_locked_for_a_moment_is_read_again(token_file, monkeypatch):
    from pathlib import Path

    real = Path.read_text
    locked = [2]

    def read_text(self, *a, **kw):
        if self == token_file and locked[0]:
            locked[0] -= 1
            raise PermissionError(13, "The process cannot access the file")
        return real(self, *a, **kw)
    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(sp, "WINDOWS", True)
    naps = []
    assert sp.load_token(token_file, sleep=naps.append)["refresh_token"] == "AQ-refresh-secret"
    assert len(naps) == 2
    locked[0] = 100
    assert sp.load_token(token_file, attempts=3, sleep=naps.append) is None


def test_a_token_file_that_stays_unreadable_isnt_retried_off_windows(
        token_file, monkeypatch, caplog):
    """On Linux/the Pi a PermissionError means the file isn't ours (written
    by root with sudo): no 1 s of retries on every call, and say so."""
    from pathlib import Path

    def read_text(self, *a, **kw):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(sp, "WINDOWS", False)
    naps = []
    with caplog.at_level("WARNING", logger=sp.log.name):
        assert sp.load_token(token_file, sleep=naps.append) is None
    assert naps == []
    assert "permission" in caplog.text and "locked" not in caplog.text


def test_device_names_are_marked_untrusted(token_file):
    player, _ = make_player(token_file, {("GET", "/me/player/devices"): Resp(200, DEVICES),
                                         ("PUT", "/me/player/play"): NO_DEVICE},
                            local="OTHER-PC")
    assert player.devices()["untrusted_content"] is True
    assert player.resume()["untrusted_content"] is True  # "which device?" names them
    assert player.transfer("phone")["untrusted_content"] is True
    assert '"untrusted_content": true' in json.dumps(player.devices())
    plain, _ = make_player(token_file)
    assert "untrusted_content" not in plain.pause()  # no names: no taint


def test_play_forgets_a_wake_word_pause(token_file):
    player, http = make_player(token_file, {("GET", "/me/player"): Resp(200, PLAYER),
                                            ("GET", "/me/playlists"): Resp(200, PLAYLISTS)})
    player.pause_for_listening()
    player._pausing.join(5)
    player.play("study")  # Kyle chose what plays now
    assert player.resume_after_listening() is False
    assert [c.json for c in http.to("PUT", "/me/player/play")] == [
        {"context_uri": "spotify:playlist:pl1"}]


def test_resume_waits_for_a_pause_still_on_its_way(token_file):
    import threading

    gate = threading.Event()

    def slow_state(call):
        gate.wait(5)
        return Resp(200, PLAYER)
    player, http = make_player(token_file, {("GET", "/me/player"): slow_state})
    player.pause_for_listening()
    threading.Timer(0.2, gate.set).start()
    assert player.resume_after_listening() is True  # waited, then resumed
    assert http.to("PUT", "/me/player/play")[0].params == {"device_id": "pc1"}


def test_turn_it_up_from_zero(token_file):
    silent = {**PLAYER, "device": {**PLAYER["device"], "volume_percent": 0}}
    player, http = make_player(token_file, {("GET", "/me/player"): Resp(200, silent)})
    assert player.volume()["volume"] == 15
    assert http.to("PUT", "/me/player/volume")[0].params["volume_percent"] == 15


# --- the wake loop's wiring (cli.py) ------------------------------------------------

class Wakes:
    """A listener: each item of `script` is one wake (a recording or None
    for "no command"); then the file ends. Follow-ups hear nothing."""

    def __init__(self, script, events):
        self.script, self.events = list(script), events

    def listen(self, source):
        if not self.script:
            return False, None
        self.events.append("wake")
        return True, self.script.pop(0)

    def record_follow_up(self, source, seconds):
        self.events.append("follow-up")
        return None


def wake_loop(fake_client, script, follow_up, on_idle):
    from assistant.chat import ClaudeChat
    from tests.conftest import make_response, text_block

    events = []
    for _ in script:
        fake_client.queue.append(make_response(text_block("Sure.")))
    chat = ClaudeChat(client=fake_client, model="m")
    cli.run_wake(chat, SimpleNamespace(speak=lambda t: events.append("speak")),
                 SimpleNamespace(transcribe=lambda r: "what time is it"),
                 Wakes(script, events), None, output_fn=lambda _: None,
                 follow_up_seconds=follow_up, on_idle=lambda: on_idle(events))
    return events


def test_idle_between_wakes_with_a_follow_up_window(fake_client):
    events = wake_loop(fake_client, [b"a", b"b"], 5,
                       lambda ev: ev.append("idle"))
    assert events[:8] == ["wake", "speak", "follow-up", "idle",
                          "wake", "speak", "follow-up", "idle"]


def test_idle_between_wakes_without_a_follow_up_window(fake_client):
    events = wake_loop(fake_client, [b"a", b"b"], 0, lambda ev: ev.append("idle"))
    assert events[:6] == ["wake", "speak", "idle", "wake", "speak", "idle"]


def test_idle_after_a_wake_with_no_command(fake_client):
    events = wake_loop(fake_client, [None, b"b"], 0, lambda ev: ev.append("idle"))
    assert events[:5] == ["wake", "idle", "wake", "speak", "idle"]


def test_a_failing_idle_hook_never_stops_listening(fake_client, caplog):
    def boom(ev):
        ev.append("idle")
        raise RuntimeError("Spotify down")
    events = wake_loop(fake_client, [b"a", b"b"], 0, boom)
    assert events.count("speak") == 2  # the second wake was still served
    assert any("Back-to-idle hook failed" in r.getMessage() for r in caplog.records)


def test_wake_components_pause_music_before_the_chime(monkeypatch):
    from assistant import wakeword

    calls = []
    detector = SimpleNamespace(frame_length=1280, phrase="Hey Claude")
    monkeypatch.setattr(wakeword, "make_detector", lambda **kw: detector)
    monkeypatch.setattr(wakeword, "MicSource", lambda *a, **kw: "mic")
    monkeypatch.setattr(wakeword, "WakeListener", lambda det, **kw: SimpleNamespace(**kw))
    monkeypatch.setattr(cli.audio, "play", lambda *_: calls.append("chime"))
    monkeypatch.setattr(cli.audio, "resolve_mic_device", lambda v: None)
    args = SimpleNamespace(keyword_path=None, audio_file=None)
    config = load_config(None, None)
    music = SimpleNamespace(pause_for_listening=lambda: calls.append("pause"))
    listener, source, phrase = cli.build_wake_components(args, config, music)
    listener.on_wake()
    assert calls == ["pause", "chime"]
    listener, *_ = cli.build_wake_components(args, config)  # no Spotify: chime only
    calls.clear()
    listener.on_wake()
    assert calls == ["chime"]


def test_wake_mode_wires_pause_and_resume(tmp_path, token_file, monkeypatch):
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", CID)
    config = spotify_config(tmp_path, token_file)
    tools = cli.build_tools(config, lambda _: None)
    player = sp.player_of(tools)
    seen = {}
    listener = SimpleNamespace(record_reply=lambda source: None)
    monkeypatch.setattr(cli, "build_wake_components",
                        lambda args, cfg, music=None: (seen.update(music=music)
                                                       or (listener, "src", "Hey Claude")))
    monkeypatch.setattr(cli, "run_wake", lambda *a, **kw: seen.update(kw))
    args = SimpleNamespace(audio_file=None, timings=False)
    cli.run_wake_mode(args, config, None, None, None, tools)
    assert seen["music"] is player
    assert seen["on_idle"] == player.resume_after_listening
    args.audio_file = "test.wav"  # a file: never touch Spotify
    cli.run_wake_mode(args, config, None, None, None, tools)
    assert seen["music"] is None and seen["on_idle"] is None
