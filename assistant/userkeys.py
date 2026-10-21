"""Secrets people bring themselves (bring your own key), encrypted at rest.

A beta tester pays for their own Claude use: they paste their Anthropic API
key in the phone app (userkeys_web.py), and family.Family makes their
conversations' client from it. The key is kept in
`<[family] dir>/<id>/anthropic_key.enc`, written privately and atomically
(google_auth.write_private), as a Fernet token (AES-128-CBC + HMAC-SHA256,
from `cryptography`). It is deleted with the rest of their folder when they
are removed.

The Fernet key is derived with HKDF-SHA256 from the server secret
USER_DATA_KEY (env only; Render generates it) and a per-purpose label, so
the same secret can later encrypt other things (e.g. Notion tokens) under
another label without one ciphertext ever opening as the other.

Fails closed: without USER_DATA_KEY (or with one too short to be a real
secret), nothing is stored and save() says why. If USER_DATA_KEY changes,
old files no longer decrypt: the person is treated as having no key and is
asked to enter it again; nothing crashes. The key itself is never logged,
returned, or put in an exception message; only its last four characters
are ever shown back (GET /api/me/anthropic-key).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from pathlib import Path

log = logging.getLogger(__name__)

ENV = "USER_DATA_KEY"
MIN_SECRET = 32                      # characters; Render's generated values are longer
ANTHROPIC_FILE = "anthropic_key.enc"  # in <dir>/<id>/
ANTHROPIC_LABEL = b"hey-claude/anthropic-api-key/v1"  # the HKDF info for these keys
KEY_PREFIX = "sk-ant-"
MAX_KEY = 256
_KEY = re.compile(r"^sk-ant-[A-Za-z0-9_-]{16,249}$")


class MissingKey(Exception):
    """A beta tester (or anyone without the owner's key to fall back on) has
    no usable Anthropic key: raised before any API call (family.py), said
    as i18n "no_key" (cli.friendly_error)."""

    def __init__(self, message: str = "no Anthropic API key for this account"):
        super().__init__(message)


class KeyProblem(ValueError):
    """A key that can't be saved; `status` is the HTTP answer (400 a bad
    key, 503 the server can't store or check keys right now). The message
    never holds the key."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def secret_problem(environ=None) -> str | None:
    """Why keys can't be stored here, or None if USER_DATA_KEY is usable."""
    secret = (os.environ if environ is None else environ).get(ENV, "")
    if not secret:
        return f"{ENV} isn't set on this server, so keys can't be stored safely"
    if len(secret) < MIN_SECRET:
        return f"{ENV} is too short (at least {MIN_SECRET} characters)"
    return None


def fernet(label: bytes, environ=None):
    """The Fernet for `label`, derived from USER_DATA_KEY; None without a
    usable secret."""
    if secret_problem(environ) is not None:
        return None
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    secret = (os.environ if environ is None else environ)[ENV]
    raw = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
               info=label).derive(secret.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(raw))


def clean_key(key) -> str:
    """An Anthropic API key as pasted (surrounding spaces dropped), or
    KeyProblem(400). Checks the shape only; check_key asks Anthropic."""
    if not isinstance(key, str):
        raise KeyProblem("paste your Anthropic API key")
    key = key.strip()
    if not key.startswith(KEY_PREFIX) or len(key) > MAX_KEY or not _KEY.match(key):
        raise KeyProblem("that doesn't look like an Anthropic API key "
                        f"(it starts with {KEY_PREFIX})")
    return key


def last4(key: str) -> str:
    return key[-4:]


def user_client(key: str, **options):
    """An Anthropic client on `key` alone. SDKs before 0.98 also read
    ANTHROPIC_AUTH_TOKEN from the environment when api_key is given and
    send it as a bearer token, so it is cleared after construction."""
    import anthropic

    client = anthropic.Anthropic(api_key=key, **options)
    client.auth_token = None
    return client


def check_key(key: str) -> None:
    """Ask Anthropic whether `key` works (one cheap read-only call). Raises
    KeyProblem 400 if it was refused, 503 if the check couldn't run (network,
    Anthropic down or rate limited). Never logs the key."""
    import anthropic

    try:
        user_client(key, timeout=10, max_retries=1).models.list(limit=1)
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
        raise KeyProblem("Anthropic didn't accept that key") from None
    except anthropic.APIStatusError as e:
        if e.status_code in (408, 409, 429) or e.status_code >= 500:
            raise KeyProblem("couldn't check the key with Anthropic; try again", 503) from None
        raise KeyProblem("Anthropic didn't accept that key") from None
    except anthropic.APIConnectionError:  # timeouts too
        raise KeyProblem("couldn't reach Anthropic to check the key; try again", 503) from None


class KeyStore:
    """Each person's Anthropic key in `<dir>/<id>/anthropic_key.enc`.
    `environ` (default os.environ, read on every call) holds USER_DATA_KEY."""

    def __init__(self, folder: str | Path, environ=None):
        self.dir = Path(folder)
        self.environ = environ

    def path(self, uid: str) -> Path:
        return self.dir / uid / ANTHROPIC_FILE

    def problem(self) -> str | None:
        return secret_problem(self.environ)

    def save(self, uid: str, key: str) -> None:
        """Encrypt and store `key` (already checked). KeyProblem 503 without a
        usable USER_DATA_KEY: nothing is written."""
        from assistant.tools.google_auth import write_private

        box = fernet(ANTHROPIC_LABEL, self.environ)
        if box is None:
            raise KeyProblem(self.problem() or "keys can't be stored", 503)
        # the owner's id inside the ciphertext: a file copied into someone
        # else's folder doesn't open as theirs
        token = box.encrypt(json.dumps({"uid": uid, "key": key}).encode("utf-8"))
        path = self.path(uid)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_private(path, token.decode("ascii"))
        log.info("User keys: saved an Anthropic key for %s", uid)

    def load(self, uid: str) -> str | None:
        """Their key, or None: none saved, no USER_DATA_KEY, or a file that
        no longer opens (USER_DATA_KEY changed, or it was tampered with)."""
        path = self.path(uid)
        try:
            token = path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as e:
            log.warning("User keys: %s's key file is unreadable (%s)", uid, type(e).__name__)
            return None
        box = fernet(ANTHROPIC_LABEL, self.environ)
        if box is None:
            log.warning("User keys: %s has a key but %s isn't usable", uid, ENV)
            return None
        try:
            data = json.loads(box.decrypt(token.strip()))
        except Exception as e:  # InvalidToken (a new USER_DATA_KEY), bad JSON
            log.warning("User keys: %s's saved key doesn't open (%s); they need to "
                        "enter it again", uid, type(e).__name__)
            return None
        key = data.get("key") if isinstance(data, dict) else None
        if not isinstance(key, str) or data.get("uid") != uid:
            log.warning("User keys: %s's key file isn't theirs; ignored", uid)
            return None
        return key

    def stored(self, uid: str) -> bool:
        """A file exists (it may not open any more)."""
        return self.path(uid).exists()

    def delete(self, uid: str) -> bool:
        """Delete their key file; False if there was none."""
        from assistant.family import _unlink

        path = self.path(uid)
        had = path.exists()
        _unlink(path)
        if had:
            log.info("User keys: deleted %s's Anthropic key", uid)
        return had
