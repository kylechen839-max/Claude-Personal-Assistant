"""Web Push to the phone app: "Enable notifications" in Settings, then the
student briefings (proactive.py) arrive on the lock screen.

On an iPhone this only works in the app added to the Home Screen (iOS
16.4+), and the permission is asked from a tap (brief.js). The browser
makes a subscription (an endpoint at Apple's, Google's, Mozilla's or
Microsoft's push service plus two keys) bound to this server's VAPID
public key, and POSTs it here.

  GET  /api/push/key          {"key": the VAPID public key (base64url)}
  POST /api/push/subscribe    {"subscription": PushSubscription.toJSON()}
  POST /api/push/unsubscribe  {"endpoint": ...}

(routes in proactive.register, beside GET /api/student/brief).

VAPID: the private key comes from the environment (VAPID_PRIVATE_KEY, a
raw P-256 key in base64url, or DER/PEM) or is generated once and kept in
`<[push] owner_dir>/vapid_private.txt`, written privately and atomically
(google_auth.write_private). It never leaves the server and is never
logged; a new key means every phone has to tap "Enable notifications"
again.

Subscriptions are each person's own, at most MAX_DEVICES (the oldest is
dropped), sealed with userkeys.seal under PUSH_LABEL and the person's id:
the owner's in `<owner_dir>/push.enc`, an invited person's in
`<[family] dir>/<id>/push.enc` (deleted with their folder). Without a
usable USER_DATA_KEY nothing is stored (subscribe answers 503). An
endpoint must be https on a known push service (PUSH_HOSTS), so a
subscription can't make this server POST to an address of the caller's
choosing. A push service's 404 or 410 (the phone unsubscribed or the app
was deleted) deletes that subscription.

The payload ({title, body, url}) is encrypted for the phone (RFC 8291,
aes128gcm): the push service can't read it. Logs say ids, counts and HTTP
statuses at INFO; a notification's title and text only at DEBUG.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

# userkeys HKDF info; keeps its pre-rebrand name (a new one would make the
# stored subscriptions unreadable)
PUSH_LABEL = b"hey-claude/push-subscriptions/v1"
PUSH_FILE = "push.enc"                            # in <dir>/<id>/ or owner_dir
VAPID_FILE = "vapid_private.txt"                  # in owner_dir
VAPID_ENV = "VAPID_PRIVATE_KEY"
MAX_DEVICES = 5
MAX_ENDPOINT = 2048
MAX_KEY_FIELD = 256
MAX_TITLE = 80
MAX_BODY = 300
# Push services a subscription may point at (the host, or a subdomain of it)
PUSH_HOSTS = ("push.apple.com", "fcm.googleapis.com", "android.googleapis.com",
              "push.services.mozilla.com", "notify.windows.com")
_B64URL = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")
_CONTROL = re.compile(r"[\x00-\x20\x7f]")  # and spaces: none in our own paths
GONE = (404, 410)  # the subscription no longer exists at the push service


def retryable(status: int | None) -> bool:
    """Worth trying again later: no answer (a network error), 429 or a 5xx.
    Any other 4xx (a changed VAPID key, a refused JWT, a payload too big)
    won't get better by itself."""
    return status is None or status == 429 or status >= 500


class PushProblem(ValueError):
    """A subscription that can't be saved; `status` is the HTTP answer.
    The message never holds the endpoint or keys."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def push_host_ok(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(host == h or host.endswith("." + h) for h in PUSH_HOSTS)


def clean_subscription(sub) -> dict:
    """{endpoint, keys: {p256dh, auth}} from PushSubscription.toJSON(), or
    PushProblem(400)."""
    if not isinstance(sub, dict):
        raise PushProblem("send the browser's push subscription")
    endpoint = sub.get("endpoint")
    keys = sub.get("keys") if isinstance(sub.get("keys"), dict) else {}
    if not isinstance(endpoint, str) or not endpoint or len(endpoint) > MAX_ENDPOINT:
        raise PushProblem("that push subscription has no usable endpoint")
    try:
        parts = urlsplit(endpoint)
        port = parts.port  # ValueError for :99999 or :abc
    except ValueError:
        raise PushProblem("that push subscription has no usable endpoint") from None
    if parts.scheme != "https" or parts.username or parts.password or port not in (
            None, 443) or not push_host_ok(parts.hostname or ""):
        raise PushProblem("that push service isn't supported")
    out = {}
    for name in ("p256dh", "auth"):
        value = keys.get(name)
        if not isinstance(value, str) or not value or len(value) > MAX_KEY_FIELD \
                or not _B64URL.match(value):
            raise PushProblem("that push subscription's keys are missing")
        out[name] = value
    return {"endpoint": endpoint, "keys": out}


def safe_path(url) -> str:
    """`url` if it is a path on this site, else the brief. No control
    characters, spaces or backslashes: browsers drop tabs and newlines, so
    "/<tab>/evil.example" would open evil.example."""
    if not isinstance(url, str) or not url.startswith("/") or url.startswith("//") \
            or "\\" in url or _CONTROL.search(url):
        return "/#brief"
    try:
        parts = urlsplit(url)
    except ValueError:
        return "/#brief"
    return url if not parts.scheme and not parts.netloc else "/#brief"


def _webpush(**kwargs):
    from pywebpush import webpush

    return webpush(**kwargs)


def _status_of(error) -> int | None:
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


class Push:
    """Subscriptions and sending, without HTTP. `family` (family.Family or
    None) says where an invited person's folder is; `send_fn` is
    pywebpush.webpush (a seam for tests)."""

    def __init__(self, config: dict, family=None, environ=None,
                 send_fn: Callable | None = None, clock=time.time):
        p = config["push"]
        self.config = config
        self.enabled = bool(p["enabled"])
        self.owner_dir = Path(p["owner_dir"])
        self.subject = str(p["subject"] or "")
        self.max_devices = max(1, min(int(p["max_devices"]), MAX_DEVICES))
        self.ttl = int(p["ttl"])
        self.timeout = float(p["timeout"])
        self.family = family
        self.environ = environ
        self.send_fn = send_fn or _webpush
        self.clock = clock
        self.lock = threading.RLock()
        self._vapid = None

    # ---------- where things are ----------
    def folder(self, uid: str | None) -> Path | None:
        """The owner's [push] owner_dir, or an invited person's own folder;
        None for someone who isn't (or is no longer) listed."""
        if uid is None:
            return self.owner_dir
        if self.family is None:
            return None
        from kwil.family import valid_id

        if not valid_id(uid) or self.family.get(uid) is None:
            return None
        return Path(self.family.dir) / uid

    def _env(self):
        if self.environ is not None:
            return self.environ
        keys = getattr(self.family, "keys", None)
        return getattr(keys, "environ", None) or os.environ

    def storage_problem(self) -> str | None:
        from kwil import userkeys

        return userkeys.secret_problem(self._env())

    # ---------- VAPID ----------
    def vapid(self):
        """The py_vapid.Vapid signing key: VAPID_PRIVATE_KEY, else the
        saved one, else a new one saved now."""
        with self.lock:
            if self._vapid is not None:
                return self._vapid
            from py_vapid import Vapid

            raw = (self._env().get(VAPID_ENV) or "").strip()
            if raw:
                self._vapid = (Vapid.from_pem(raw.encode()) if "-----BEGIN" in raw
                               else Vapid.from_string(raw))
                return self._vapid
            path = self.owner_dir / VAPID_FILE
            if path.exists():
                self._vapid = Vapid.from_string(path.read_text(encoding="ascii").strip())
                return self._vapid
            from cryptography.hazmat.primitives.asymmetric import ec

            from kwil.tools.google_auth import write_private

            key = ec.generate_private_key(ec.SECP256R1())
            raw = _b64url(key.private_numbers().private_value.to_bytes(32, "big"))
            path.parent.mkdir(parents=True, exist_ok=True)
            write_private(path, raw + "\n")
            log.info("Push: made this server's VAPID key (%s)", path.name)
            self._vapid = Vapid.from_string(raw)
            return self._vapid

    def public_key(self) -> str:
        """applicationServerKey for pushManager.subscribe (uncompressed P-256)."""
        from cryptography.hazmat.primitives import serialization

        point = self.vapid().public_key.public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        return _b64url(point)

    def claims_subject(self) -> str:
        """The VAPID `sub` claim: [push] subject, else this server's public
        https address (Apple refuses a token without a mailto: or https: one)."""
        if self.subject:
            return self.subject
        from kwil.family import own_origin

        try:
            origin = own_origin(self.config)
        except Exception:  # noqa: BLE001 - no public address configured
            origin = ""
        return origin if origin.startswith("https://") else "mailto:kwil@localhost"

    # ---------- subscriptions ----------
    def _path(self, uid: str | None) -> Path | None:
        folder = self.folder(uid)
        return folder / PUSH_FILE if folder is not None else None

    def subscriptions(self, uid: str | None) -> list[dict]:
        from kwil import userkeys

        path = self._path(uid)
        if path is None or not path.exists():
            return []
        try:
            data = userkeys.unseal(PUSH_LABEL, uid or "owner", path.read_bytes(),
                                   self._env(), what="push subscriptions",
                                   redo="enable notifications again")
        except OSError as e:
            log.warning("Push: reading %s's subscriptions failed (%s)", uid or "owner",
                        type(e).__name__)
            return []
        subs = (data or {}).get("subs")
        return [s for s in subs if isinstance(s, dict)][:MAX_DEVICES] \
            if isinstance(subs, list) else []

    def has_devices(self, uid: str | None) -> bool:
        path = self._path(uid)
        return path is not None and path.exists() and bool(self.subscriptions(uid))

    def _save(self, uid: str | None, subs: list[dict]) -> None:
        from kwil import userkeys
        from kwil.tools.google_auth import write_private

        path = self._path(uid)
        if path is None:
            raise PushProblem("not found", 404)
        if not subs:
            path.unlink(missing_ok=True)
            return
        sealed = userkeys.seal(PUSH_LABEL, uid or "owner", {"subs": subs}, self._env())
        path.parent.mkdir(parents=True, exist_ok=True)
        write_private(path, sealed)

    def subscribe(self, uid: str | None, sub) -> int:
        """Save one device's subscription; returns how many they have.
        PushProblem: 400 a bad subscription, 404 not listed, 503 the server
        can't store it (no usable USER_DATA_KEY)."""
        clean = clean_subscription(sub)
        if self.folder(uid) is None:
            raise PushProblem("not found", 404)
        problem = self.storage_problem()
        if problem is not None:
            raise PushProblem("this server can't store notification settings yet "
                              "(its owner needs to set USER_DATA_KEY)", 503)
        with self.lock:
            subs = [s for s in self.subscriptions(uid) if s.get("endpoint") != clean["endpoint"]]
            subs.append({**clean, "added": int(self.clock())})
            subs = subs[-self.max_devices:]
            self._save(uid, subs)
        log.info("Push: %s subscribed a device (%d in all)", uid or "owner", len(subs))
        return len(subs)

    def unsubscribe(self, uid: str | None, endpoint) -> bool:
        if not isinstance(endpoint, str) or not endpoint:
            return False
        with self.lock:
            subs = self.subscriptions(uid)
            left = [s for s in subs if s.get("endpoint") != endpoint]
            if len(left) == len(subs):
                return False
            self._save(uid, left)
        log.info("Push: %s removed a device (%d left)", uid or "owner", len(left))
        return True

    # ---------- sending ----------
    def send(self, uid: str | None, title: str, body: str, url: str = "/#brief") -> int:
        """Notify every device of `uid`; returns how many took it."""
        return self.deliver(uid, title, body, url)["delivered"]

    def deliver(self, uid: str | None, title: str, body: str, url: str = "/#brief",
                endpoints=None) -> dict:
        """Notify `uid`'s devices (only those `endpoints`, if given):
        {"delivered", "failed", "gone", "refused"} counts. A device the
        push service says is gone (404/410) is forgotten; "failed" is worth
        trying again later (a network error, 429, a 5xx); "refused" is any
        other 4xx, which a retry won't fix (logged with its status)."""
        result = {"delivered": 0, "failed": 0, "gone": 0, "refused": 0}
        if not self.enabled:
            return result
        subs = self.subscriptions(uid)
        if endpoints is not None:
            wanted = set(endpoints)
            subs = [s for s in subs if s.get("endpoint") in wanted]
        if not subs:
            return result
        url = safe_path(url)
        payload = json.dumps({"title": str(title)[:MAX_TITLE], "body": str(body)[:MAX_BODY],
                              "url": url})
        log.debug("Push to %s: %r / %r", uid or "owner", title, body)
        vapid = self.vapid()
        delivered, gone = 0, []
        for sub in subs:
            info = {"endpoint": sub["endpoint"], "keys": sub["keys"]}
            try:
                self.send_fn(subscription_info=info, data=payload, vapid_private_key=vapid,
                             vapid_claims={"sub": self.claims_subject()},
                             ttl=self.ttl, timeout=self.timeout)
                delivered += 1
            except Exception as e:  # noqa: BLE001 - WebPushException, network errors
                status = _status_of(e)
                if status in GONE:
                    gone.append(sub["endpoint"])
                elif retryable(status):
                    result["failed"] += 1
                else:
                    result["refused"] += 1
                log.warning("Push to a device of %s failed (%s%s)", uid or "owner",
                            type(e).__name__, f" {status}" if status else "")
        if gone:
            with self.lock:
                left = [s for s in self.subscriptions(uid) if s.get("endpoint") not in gone]
                try:
                    self._save(uid, left)
                except Exception as e:  # noqa: BLE001 - tried again next time
                    log.warning("Push: forgetting %s's gone devices failed (%s)",
                                uid or "owner", type(e).__name__)
            log.info("Push: forgot %d gone device(s) of %s", len(gone), uid or "owner")
        log.info("Push to %s: %d of %d device(s)", uid or "owner", delivered, len(subs))
        result.update(delivered=delivered, gone=len(gone))
        return result


def from_config(config: dict, family=None) -> Push | None:
    """The server's Push, or None with [push] enabled off."""
    if not config["push"]["enabled"]:
        return None
    return Push(config, family)
