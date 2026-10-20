"""Spotify tools: play, pause, skip, volume, what's playing, devices.

They drive the Spotify Web API's player, which controls a Spotify app that
is already open somewhere (the PC's desktop app, the phone, a speaker): the
assistant itself plays no audio. Player commands need Spotify Premium.

Sign-in is OAuth Authorization Code with PKCE (no client secret): Kyle's own
Spotify app's client id is SPOTIFY_CLIENT_ID in .env, and the token is
written privately to [spotify] token. Sign in once with
`python -m assistant.tools.spotify`, or the phone app's "Connect Spotify"
(spotify_web.py). Only the scopes below are asked for: nothing here can
change playlists or the library.

Playback control is harmless and never asks for a yes. Track, album, artist
and playlist names come from other people, so results that hold them are
marked untrusted (a later state-changing tool then asks first).
"""

from __future__ import annotations

import base64
import difflib
import hashlib
import json
import logging
import os
import re
import secrets
import socket
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from assistant import i18n
from assistant.tools import UNTRUSTED_MARK, Tool
from assistant.tools.google_auth import write_private

log = logging.getLogger(__name__)

CLIENT_ID_ENV = "SPOTIFY_CLIENT_ID"
AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"
# /me/player (now playing) is covered by user-read-playback-state, so
# user-read-currently-playing isn't asked for.
SCOPES = ("user-read-playback-state", "user-modify-playback-state",
          "playlist-read-private")
SIGN_IN_COMMAND = "python -m assistant.tools.spotify"
REFRESH_MARGIN = 60  # seconds: refresh a token this close to expiring
PLAYLIST_TTL = 300   # seconds to reuse Kyle's playlist list
MAX_PLAYLISTS = 200  # pages of 50; plenty for one person
# "play on my phone": Spotify device types for the words people use
DEVICE_WORDS = {
    "Computer": ("pc", "computer", "laptop", "desktop", "mac", "电脑", "笔记本"),
    "Smartphone": ("phone", "iphone", "mobile", "cell", "手机"),
    "Speaker": ("speaker", "音箱", "音响"),
    "TV": ("tv", "television", "电视"),
    "Tablet": ("tablet", "ipad", "平板"),
}
KINDS = ("auto", "playlist", "track", "album", "artist")


class SpotifyError(RuntimeError):
    """A Spotify request failed. `reason` picks the spoken message:
    premium, no_device, no_volume, signin, rate_limited, unreachable, error."""

    def __init__(self, reason: str, status: int = 0):
        self.reason, self.status = reason, status
        super().__init__(f"Spotify {reason} (HTTP {status})" if status
                         else f"Spotify {reason}")


def spoken_error(e: SpotifyError) -> dict:
    """A tool result Claude can read out, in the assistant's language."""
    key = {"premium": "spotify_premium", "no_device": "spotify_no_device",
           "no_volume": "spotify_no_volume", "signin": "spotify_signin",
           "rate_limited": "spotify_busy", "unreachable": "spotify_unreachable",
           }.get(e.reason, "spotify_error")
    return {"status": "error", "reason": e.reason, "message": i18n.t(key)}


# --- sign-in (PKCE) ------------------------------------------------------------

def client_id(environ=os.environ) -> str:
    return environ.get(CLIENT_ID_ENV, "").strip()


def new_verifier() -> str:
    """A PKCE code verifier: 86 URL-safe characters (43-128 allowed)."""
    return secrets.token_urlsafe(64)


def challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorize_url(cid: str, redirect_uri: str, state: str, verifier: str) -> str:
    return AUTHORIZE_URL + "?" + urlencode({
        "client_id": cid, "response_type": "code", "redirect_uri": redirect_uri,
        "code_challenge_method": "S256", "code_challenge": challenge(verifier),
        "state": state, "scope": " ".join(SCOPES)})


def _http():
    import requests

    return requests.Session()


def _token_request(http, data: dict, timeout: float) -> dict:
    """POST to Spotify's token endpoint; the token JSON, or SpotifyError
    (with the OAuth error code only, never a value from the request)."""
    try:
        r = http.request("POST", TOKEN_URL, data=data, timeout=timeout,
                         headers={"Content-Type": "application/x-www-form-urlencoded"})
    except Exception as e:  # requests.ConnectionError, Timeout
        log.warning("Spotify token request failed (%s)", type(e).__name__)
        raise SpotifyError("unreachable") from None
    if r.status_code != 200:
        try:
            code = str(r.json().get("error", ""))[:40]
        except ValueError:
            code = ""
        log.warning("Spotify token request refused: HTTP %s %s", r.status_code, code)
        raise SpotifyError("signin", r.status_code)
    info = r.json()
    if not info.get("access_token"):
        raise SpotifyError("signin", r.status_code)
    return info


def exchange_code(code: str, verifier: str, redirect_uri: str, cid: str,
                  http=None, timeout: float = 10, clock=time.time) -> dict:
    """The authorization code (+ PKCE verifier) -> a token dict to save."""
    info = _token_request(http or _http(), {
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": redirect_uri, "client_id": cid,
        "code_verifier": verifier}, timeout)
    return _stored(info, None, clock)


def _stored(info: dict, old: dict | None, clock) -> dict:
    """What goes in the token file. A refresh may omit refresh_token (then
    the old one stays valid) or rotate it (then only the new one works)."""
    return {"access_token": info["access_token"],
            "refresh_token": info.get("refresh_token") or (old or {}).get("refresh_token", ""),
            "expires_at": int(clock()) + int(info.get("expires_in", 3600)),
            "scope": info.get("scope", (old or {}).get("scope", "")),
            "token_type": "Bearer"}


def save_token(path: str | Path, token: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_private(path, json.dumps(token))


# A PermissionError on the token file is a passing lock only on Windows
WINDOWS = os.name == "nt"


def load_token(path: str | Path, attempts: int = 20,
               sleep=time.sleep) -> dict | None:
    """The saved token, or None (no file, or not a usable token). On
    Windows the file can't be opened for a moment while another process
    replaces it (write_private), so a PermissionError is retried there.
    Elsewhere it means the file isn't ours (e.g. written with sudo)."""
    for attempt in range(attempts):
        try:
            info = json.loads(Path(path).read_text(encoding="utf-8"))
            break
        except FileNotFoundError:
            return None
        except PermissionError:
            if not WINDOWS:
                log.warning("Spotify token file %s is unreadable (permission)", path)
                return None
            if attempt == attempts - 1:
                log.warning("Spotify token file %s is locked", path)
                return None
            sleep(0.05)
        except (OSError, ValueError):
            log.warning("Spotify token file %s is unreadable", path)
            return None
    if not isinstance(info, dict) or not info.get("refresh_token"):
        return None
    return info


def connected(config: dict, environ=os.environ) -> bool:
    """[spotify] on, a client id, and a saved sign-in."""
    sp = config["spotify"]
    return bool(sp["enabled"] and client_id(environ) and load_token(sp["token"]))


# --- the Web API ----------------------------------------------------------------

class SpotifyClient:
    """Bearer-token requests to api.spotify.com, refreshing the token as
    needed. The token file is shared (the wake-word assistant and the web
    app may run at once, and a refresh can rotate the refresh token), so an
    expired token is re-read from the file before refreshing."""

    def __init__(self, token_path: str | Path, cid: str | None = None, http=None,
                 timeout: float = 10, clock=time.time):
        self.token_path = Path(token_path)
        self.cid = cid if cid is not None else client_id()
        self._http = http
        self.timeout = timeout
        self.clock = clock
        self.token: dict | None = None
        self.lock = threading.Lock()

    @property
    def http(self):
        if self._http is None:
            self._http = _http()
        return self._http

    def _fresh(self, token: dict | None) -> bool:
        return bool(token and token.get("access_token")
                    and token.get("expires_at", 0) - REFRESH_MARGIN > self.clock())

    def access_token(self, force_refresh: bool = False) -> str:
        with self.lock:
            if not force_refresh and self._fresh(self.token):
                return self.token["access_token"]
            on_disk = load_token(self.token_path)
            if on_disk is None:
                raise SpotifyError("signin")
            if (not force_refresh and self._fresh(on_disk)) or (
                    force_refresh and self.token
                    and on_disk.get("access_token") != self.token.get("access_token")
                    and self._fresh(on_disk)):
                self.token = on_disk  # another process refreshed it
                return on_disk["access_token"]
            try:
                info = self._refresh(on_disk["refresh_token"])
            except SpotifyError as e:
                # Another process may have refreshed (and rotated the
                # refresh token) between our read and our request: use its
                # token, or try once more with its refresh token.
                if e.reason != "signin":
                    raise
                again = load_token(self.token_path)
                if again is None or again.get("refresh_token") == on_disk["refresh_token"]:
                    raise
                log.info("Spotify token was refreshed by another process")
                if self._fresh(again):
                    self.token = again
                    return again["access_token"]
                on_disk = again
                info = self._refresh(on_disk["refresh_token"])
            self.token = _stored(info, on_disk, self.clock)
            save_token(self.token_path, self.token)
            log.info("Spotify token refreshed")
            return self.token["access_token"]

    def _refresh(self, refresh_token: str) -> dict:
        return _token_request(self.http, {
            "grant_type": "refresh_token", "client_id": self.cid,
            "refresh_token": refresh_token}, self.timeout)

    def request(self, method: str, path: str, params: dict | None = None,
                body: dict | None = None):
        """The JSON reply (None for 202/204). A 401 refreshes the token and
        tries once more (the first try did nothing); nothing else is
        retried, so a "next" never skips two songs."""
        for attempt in (0, 1):
            headers = {"Authorization": "Bearer " + self.access_token(attempt == 1)}
            try:
                r = self.http.request(method, API + path, params=params, json=body,
                                      headers=headers, timeout=self.timeout)
            except Exception as e:  # requests.ConnectionError, Timeout
                log.warning("Spotify %s %s failed (%s)", method, path.split("?")[0],
                            type(e).__name__)
                raise SpotifyError("unreachable") from None
            if r.status_code == 401 and attempt == 0:
                continue
            break
        if r.status_code in (202, 204) or (200 <= r.status_code < 300 and not r.content):
            return None
        if 200 <= r.status_code < 300:
            return r.json()
        raise self._error(method, path, r)

    @staticmethod
    def _error(method: str, path: str, r) -> SpotifyError:
        """Spotify's {"error": {"status", "message", "reason"}} as a reason."""
        try:
            err = r.json().get("error") or {}
        except ValueError:
            err = {}
        if not isinstance(err, dict):
            err = {}
        reason = str(err.get("reason", ""))
        message = str(err.get("message", "")).lower()
        log.warning("Spotify %s %s: HTTP %s %s", method, path.split("?")[0],
                    r.status_code, reason[:40])
        if r.status_code == 401:
            return SpotifyError("signin", 401)
        if r.status_code == 429:
            return SpotifyError("rate_limited", 429)
        if reason == "PREMIUM_REQUIRED" or "premium" in message:
            return SpotifyError("premium", r.status_code)
        if reason == "NO_ACTIVE_DEVICE" or "no active device" in message:
            return SpotifyError("no_device", r.status_code)
        if reason == "VOLUME_CONTROL_DISALLOW":
            return SpotifyError("no_volume", r.status_code)
        if reason in ("ALREADY_PAUSED", "ALREADY_PLAYING"):
            return SpotifyError(reason.lower(), r.status_code)
        return SpotifyError("error", r.status_code)


def _norm(text: str) -> str:
    """'My Study Playlist!' -> 'study' (for matching Kyle's playlists)."""
    text = re.sub(r"[^\w\s]", " ", str(text).lower())
    words = [w for w in text.split()
             if w not in {"my", "the", "playlist", "playlists", "a", "some"}]
    return " ".join(words).replace("我的", "").replace("歌单", "").strip()


def _names(items) -> str:
    return ", ".join(a.get("name", "") for a in items or [] if isinstance(a, dict))


class SpotifyPlayer:
    """The tools, plus pausing music on this computer while Kyle talks to
    the assistant (pause_for_listening / resume_after_listening)."""

    def __init__(self, client: SpotifyClient, local_device: str = "",
                 volume_step: int = 15, clock=time.monotonic):
        self.client = client
        self.local_device = (local_device or socket.gethostname()).strip()
        self.volume_step = int(volume_step)
        self.clock = clock
        self._playlists: tuple[float, list[dict]] | None = None
        self._me: str | None = None
        # pausing while listening
        self._paused_device: str | None = None
        self._pausing: threading.Thread | None = None
        self._pause_lock = threading.Lock()

    # ----- helpers -----
    def _devices(self) -> list[dict]:
        return [d for d in (self.client.request("GET", "/me/player/devices") or {})
                .get("devices", []) if isinstance(d, dict)]

    def _is_local(self, device: dict | None) -> bool:
        return bool(device and self.local_device
                    and str(device.get("name", "")).strip().lower()
                    == self.local_device.lower())

    def _find_device(self, wanted: str) -> dict | None:
        """A device by name ("Kyle's iPhone"), or by what it is ("my phone",
        "the PC", "this computer")."""
        devices = [d for d in self._devices() if not d.get("is_restricted")]
        w = wanted.strip().lower()
        if not w:
            return None
        if w in ("this computer", "this pc", "here", "这台电脑", "这里"):
            return next((d for d in devices if self._is_local(d)), None)
        for d in devices:  # the exact name
            if str(d.get("name", "")).lower() == w:
                return d
        for d in devices:  # part of the name
            if w in str(d.get("name", "")).lower() or str(d.get("name", "")).lower() in w:
                return d
        for kind, words in DEVICE_WORDS.items():
            if any(word in w for word in words):
                typed = [d for d in devices if d.get("type") == kind]
                local = [d for d in typed if self._is_local(d)]
                if local or typed:  # "my PC": this computer first
                    return (local or typed)[0]
        return None

    def _pick_device(self) -> dict | None:
        """With no active device: this computer's Spotify if it is open, or
        the only one open. None if there is no clear choice."""
        devices = [d for d in self._devices() if not d.get("is_restricted")]
        local = [d for d in devices if self._is_local(d)]
        if local:
            return local[0]
        return devices[0] if len(devices) == 1 else None

    def _no_device(self) -> dict:
        """No active device and no clear choice: say which are open."""
        try:
            names = [d.get("name", "") for d in self._devices()]
        except SpotifyError:
            names = []
        if not names:
            return spoken_error(SpotifyError("no_device"))
        return {"status": "error", "reason": "which_device", "devices": names,
                "message": i18n.t("spotify_which_device", devices=", ".join(names)),
                UNTRUSTED_MARK: True}

    def _control(self, method: str, path: str, params=None, body=None,
                 device_id: str | None = None):
        """A player command; with no active device, retried once on this
        computer's (or the only open) Spotify. Raises SpotifyError."""
        params = dict(params or {})
        if device_id:
            params["device_id"] = device_id
        try:
            return self.client.request(method, path, params or None, body)
        except SpotifyError as e:
            if e.reason != "no_device" or device_id:
                raise
        device = self._pick_device()
        if device is None:
            raise SpotifyError("no_device")
        params["device_id"] = device["id"]
        self.client.request(method, path, params, body)
        return device

    def _my_id(self) -> str | None:
        if self._me is None:
            try:
                self._me = (self.client.request("GET", "/me") or {}).get("id") or ""
            except SpotifyError:
                return None
        return self._me or None

    def _my_playlists(self) -> list[dict]:
        if self._playlists and self.clock() - self._playlists[0] < PLAYLIST_TTL:
            return self._playlists[1]
        found, offset = [], 0
        while offset < MAX_PLAYLISTS:
            page = self.client.request("GET", "/me/playlists",
                                       {"limit": 50, "offset": offset}) or {}
            items = [p for p in page.get("items") or [] if isinstance(p, dict)]
            found += items
            if not page.get("next") or not items:
                break
            offset += 50
        self._playlists = (self.clock(), found)
        return found

    def _match_playlist(self, query: str) -> dict | None:
        q = _norm(query)
        if not q:
            return None
        best, best_score = None, 0.0
        for p in self._my_playlists():
            name = _norm(p.get("name", ""))
            if not name:
                continue
            if name == q:
                score = 1.0
            elif q in name.split() or name in q.split() or (
                    len(q) >= 4 and (q in name or name in q)):
                score = 0.9
            else:
                score = difflib.SequenceMatcher(None, q, name).ratio()
            if score > best_score:
                best, best_score = p, score
        return best if best_score >= 0.8 else None

    def _search(self, query: str, kind: str) -> tuple[str, dict] | None:
        types = ["track", "playlist", "artist", "album"] if kind == "auto" else [kind]
        found = self.client.request("GET", "/search", {
            "q": query, "type": ",".join(types), "limit": 5}) or {}
        # Spotify's search lists may hold null entries (removed playlists)
        items = {t: [i for i in (found.get(t + "s") or {}).get("items") or []
                     if isinstance(i, dict) and i.get("uri")] for t in types}
        if kind != "auto":
            return (kind, items[kind][0]) if items[kind] else None
        q = _norm(query)
        for t in ("track", "artist", "album"):  # a song or artist by name
            for item in items[t][:3]:
                if _norm(item.get("name", "")) == q:
                    return t, item
        # a mood or genre ("some lo-fi"): a playlist, else the best song
        for t in ("playlist", "track", "artist", "album"):
            if items[t]:
                return t, items[t][0]
        return None

    @staticmethod
    def _describe(kind: str, item: dict) -> dict:
        info = {"kind": kind, "name": item.get("name", "")}
        if kind in ("track", "album"):
            info["by"] = _names(item.get("artists"))
        elif kind == "playlist":
            info["by"] = (item.get("owner") or {}).get("display_name", "")
        return info

    def _forget_pause(self) -> None:
        """Kyle controlled playback himself: don't resume afterwards (once
        a wake-word pause still on its way has landed)."""
        thread = self._pausing
        if thread is not None and thread is not threading.current_thread():
            thread.join(5)
        with self._pause_lock:
            self._paused_device = None

    # ----- tools -----
    def play(self, query: str = "", kind: str = "auto", device: str = "") -> dict:
        self._forget_pause()
        try:
            target = None
            if device:
                target = self._find_device(device)
                if target is None:
                    return {"status": "error", "reason": "unknown_device",
                            "message": i18n.t("spotify_unknown_device", device=device)}
            if not query.strip():
                used = self._control("PUT", "/me/player/play",
                                     device_id=target and target["id"])
                return {"status": "playing", "resumed": True,
                        **self._where(target or used)}
            kind = kind if kind in KINDS else "auto"
            mine = (self._match_playlist(query)
                    if kind in ("auto", "playlist") else None)
            if mine is not None:
                chosen, source = ("playlist", mine), "your playlists"
            else:
                chosen, source = self._search(query, kind), "Spotify"
            if chosen is None:
                return {"status": "not_found",
                        "message": i18n.t("spotify_not_found", query=query)}
            what, item = chosen
            body = ({"uris": [item["uri"]]} if what == "track"
                    else {"context_uri": item["uri"]})
            used = self._control("PUT", "/me/player/play", body=body,
                                 device_id=target and target["id"])
            log.info("Spotify: playing a %s from %s", what,
                     "Kyle's playlists" if mine is not None else "search")
            result = {"status": "playing", "from": source,
                      **self._describe(what, item), **self._where(target or used)}
            mine_by_kyle = (mine is not None and self._my_id() is not None
                            and (mine.get("owner") or {}).get("id") == self._my_id())
            if not mine_by_kyle:  # names written by artists and other users
                result[UNTRUSTED_MARK] = True
            return result
        except SpotifyError as e:
            return self._failed(e)

    @staticmethod
    def _where(device) -> dict:
        """The device's name, marked untrusted: anyone can name a Spotify
        Connect speaker or a phone on the same account."""
        return ({"device": device.get("name", ""), UNTRUSTED_MARK: True}
                if isinstance(device, dict) and device.get("name") else {})

    def _failed(self, e: SpotifyError) -> dict:
        if e.reason == "no_device":
            return self._no_device()
        return spoken_error(e)

    def _simple(self, method: str, path: str, done: str, already: str = "") -> dict:
        self._forget_pause()
        try:
            used = self._control(method, path)
        except SpotifyError as e:
            if already and e.reason == already:
                return {"status": done}
            return self._failed(e)
        return {"status": done, **self._where(used)}

    def pause(self) -> dict:
        return self._simple("PUT", "/me/player/pause", "paused", "already_paused")

    def resume(self) -> dict:
        return self._simple("PUT", "/me/player/play", "playing", "already_playing")

    def next(self) -> dict:
        return self._simple("POST", "/me/player/next", "skipped")

    def previous(self) -> dict:
        return self._simple("POST", "/me/player/previous", "went_back")

    def volume(self, level: int | None = None, change: int | None = None) -> dict:
        try:
            state = self.client.request("GET", "/me/player")
            device = (state or {}).get("device") or {}
            if not device:
                return self._no_device()
            if device.get("supports_volume") is False:
                return spoken_error(SpotifyError("no_volume"))
            current = device.get("volume_percent")
            if level is None:
                step = self.volume_step if change is None else int(change)
                level = (current if current is not None else 50) + step
            level = max(0, min(100, int(level)))
            self.client.request("PUT", "/me/player/volume", {
                "volume_percent": level, "device_id": device.get("id")})
        except SpotifyError as e:
            return self._failed(e)
        return {"status": "volume_set", "volume": level, "was": current,
                **self._where(device)}

    def now_playing(self) -> dict:
        try:
            state = self.client.request("GET", "/me/player",
                                        {"additional_types": "episode"})
        except SpotifyError as e:
            return self._failed(e)
        item = (state or {}).get("item")
        if not state or not item:
            return {"status": "nothing_playing",
                    "message": i18n.t("spotify_nothing_playing")}
        info = {"status": "playing" if state.get("is_playing") else "paused",
                "kind": item.get("type", "track"), "name": item.get("name", ""),
                UNTRUSTED_MARK: True}
        if item.get("type") == "episode":
            info["show"] = (item.get("show") or {}).get("name", "")
        else:
            info["by"] = _names(item.get("artists"))
            info["album"] = (item.get("album") or {}).get("name", "")
        device = state.get("device") or {}
        info.update(self._where(device))
        if device.get("volume_percent") is not None:
            info["volume"] = device["volume_percent"]
        if self._paused_device:
            info["paused_while_you_talk"] = True  # resumes after this conversation
        return info

    def devices(self) -> dict:
        try:
            devices = self._devices()
        except SpotifyError as e:
            return spoken_error(e)
        if not devices:
            return spoken_error(SpotifyError("no_device"))
        return {"devices": [{"name": d.get("name", ""), "type": d.get("type", ""),
                             "active": bool(d.get("is_active")),
                             "this_computer": self._is_local(d),
                             "can_control": not d.get("is_restricted")}
                            for d in devices],
                UNTRUSTED_MARK: True}  # device names: see _where

    def transfer(self, device: str, play: bool = True) -> dict:
        self._forget_pause()
        try:
            target = self._find_device(device)
            if target is None:
                return {"status": "error", "reason": "unknown_device",
                        "message": i18n.t("spotify_unknown_device", device=device)}
            self.client.request("PUT", "/me/player",
                                body={"device_ids": [target["id"]], "play": bool(play)})
        except SpotifyError as e:
            return self._failed(e)
        return {"status": "moved", **self._where(target)}

    # ----- pause while Kyle talks to the assistant (wake mode) -----
    def pause_for_listening(self) -> None:
        """On the wake word: pause Spotify if it is playing on this
        computer (in the background: recording the request starts now)."""
        def run():
            try:
                state = self.client.request("GET", "/me/player")
                device = (state or {}).get("device") or {}
                if state and state.get("is_playing") and self._is_local(device):
                    self.client.request("PUT", "/me/player/pause",
                                        {"device_id": device.get("id")})
                    with self._pause_lock:
                        self._paused_device = device.get("id")
                    log.info("Spotify paused while listening")
            except Exception as e:  # never in the way of the conversation
                log.warning("Couldn't pause Spotify while listening (%s)",
                            type(e).__name__)

        with self._pause_lock:
            if self._pausing is not None and self._pausing.is_alive():
                return
            self._pausing = threading.Thread(target=run, name="spotify-pause",
                                             daemon=True)
            self._pausing.start()

    def resume_after_listening(self, wait: float = 5) -> bool:
        """Back to waiting for the wake word: resume what we paused, unless
        Kyle used a Spotify tool meanwhile. True if it resumed."""
        thread = self._pausing
        if thread is not None:
            thread.join(wait)
        with self._pause_lock:
            device, self._paused_device = self._paused_device, None
        if not device:
            return False
        try:
            self.client.request("PUT", "/me/player/play", {"device_id": device})
        except Exception as e:
            log.warning("Couldn't resume Spotify (%s)", type(e).__name__)
            return False
        log.info("Spotify resumed")
        return True

    def tools(self) -> list[Tool]:
        empty = {"type": "object", "properties": {}}
        return [
            Tool("spotify_play",
                 "Play music on Kyle's Spotify. Give what to play as he said it "
                 "(\"study\", \"some lo-fi\", \"Blinding Lights\"): his own "
                 "playlists are searched first, then Spotify's catalog. With no "
                 "query, resumes. Optional device (\"my phone\", \"PC\", a "
                 "device name) plays it there.",
                 {"type": "object", "properties": {
                     "query": {"type": "string",
                               "description": "Playlist, song, artist, album or mood"},
                     "kind": {"type": "string", "enum": list(KINDS),
                              "description": "What the query is, if clear "
                                             "(default auto)"},
                     "device": {"type": "string",
                                "description": "Where to play, if he said"}}},
                 self.play),
            Tool("spotify_pause", "Pause Spotify.", empty, self.pause),
            Tool("spotify_resume", "Resume Spotify where it was paused.", empty,
                 self.resume),
            Tool("spotify_next", "Skip to the next song on Spotify.", empty, self.next),
            Tool("spotify_previous", "Go back to the previous song on Spotify.",
                 empty, self.previous),
            Tool("spotify_volume",
                 "Change Spotify's volume: an exact level (0-100), or a change "
                 "(+15 for \"turn it up\", -15 for \"turn it down\").",
                 {"type": "object", "properties": {
                     "level": {"type": "integer", "minimum": 0, "maximum": 100},
                     "change": {"type": "integer", "minimum": -100, "maximum": 100}}},
                 self.volume),
            Tool("spotify_now_playing", "What's playing on Spotify right now.",
                 empty, self.now_playing),
            Tool("spotify_devices", "List the devices Spotify is open on.", empty,
                 self.devices),
            Tool("spotify_transfer",
                 "Move Spotify playback to another device (\"play on my phone\", "
                 "\"on the PC\"); give a device name or what it is.",
                 {"type": "object", "properties": {
                     "device": {"type": "string"},
                     "play": {"type": "boolean",
                              "description": "Keep playing there (default true)"}},
                  "required": ["device"]},
                 self.transfer),
        ]


def player_from_config(config: dict, http=None) -> SpotifyPlayer | None:
    """A SpotifyPlayer if [spotify] is on and signed in, else None."""
    if not connected(config):
        return None
    sp = config["spotify"]
    return SpotifyPlayer(SpotifyClient(sp["token"], http=http, timeout=sp["timeout"]),
                         local_device=sp["local_device"], volume_step=sp["volume_step"])


def player_of(registry) -> SpotifyPlayer | None:
    """The SpotifyPlayer behind a registry's Spotify tools (for pausing
    music while listening), or None."""
    tool = getattr(registry, "tools", {}).get("spotify_pause")
    owner = getattr(tool.handler, "__self__", None) if tool else None
    return owner if isinstance(owner, SpotifyPlayer) else None


# --- one-time sign-in on the PC or Pi --------------------------------------------

def sign_in(config: dict, input_fn=input, output_fn=print, http=None,
            open_browser: bool = True) -> dict:
    """Print the consent link, then take back the address the browser lands
    on ([spotify] redirect_uri; on another device it doesn't load, which is
    fine: copy it from the address bar). Saves the token privately."""
    sp = config["spotify"]
    cid = client_id()
    if not cid:
        raise SystemExit(f"Set {CLIENT_ID_ENV} in .env first (README, \"Spotify\").")
    state, verifier = secrets.token_urlsafe(24), new_verifier()
    url = authorize_url(cid, sp["redirect_uri"], state, verifier)
    output_fn("Open this link and allow access:\n\n" + url + "\n")
    if open_browser:
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception:
            pass
    landed = input_fn("Then paste the full address the browser went to: ")
    query = parse_qs(urlparse(landed.strip()).query)
    if query.get("error"):
        raise SystemExit("Spotify sign-in was not allowed.")
    if query.get("state", [""])[0] != state or not query.get("code"):
        raise SystemExit("That isn't the address from this sign-in; run it again.")
    token = exchange_code(query["code"][0], verifier, sp["redirect_uri"], cid,
                          http=http, timeout=sp["timeout"])
    save_token(sp["token"], token)
    output_fn(f"Spotify connected; token saved to {sp['token']}.")
    return token


def main() -> None:
    from assistant.config import load_config

    config = load_config()
    if not config["spotify"]["enabled"]:
        print("Note: [spotify] enabled is false in config.toml; the tools stay "
              "off until you set it to true.")
    try:
        sign_in(config)
    except SpotifyError as e:
        raise SystemExit(f"Spotify sign-in failed ({e.reason}).") from None


if __name__ == "__main__":
    main()
