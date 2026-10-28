"""Google sign-in shared by Calendar and Gmail (one OAuth client, one token).

The one-time sign-in asks for every scope the assistant uses. A token saved
before Gmail support only has the Calendar scope: Calendar keeps working
with it, and the Gmail tools say exactly what to re-run
(`python -m kwil.tools.google_auth`). The same goes for the read-only
calendar list and labels scopes (CALENDAR_EXTRA_SCOPES).

In cloud mode the token JSON comes from the GOOGLE_TOKEN_JSON environment
variable (refreshed tokens stay in memory), until the owner taps "Connect
Google account" in the app ([webapp] owner_google): from then on the token
that sign-in saved to [calendar] token wins (owner_environ), also refreshed
in memory only.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path

log = logging.getLogger(__name__)

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"
GMAIL_READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
# Needed to create drafts. Google has no drafts-only scope: gmail.compose
# also permits sending, so sending is blocked in code instead (gmail.py
# wraps the service so send calls raise; a test checks there are none).
GMAIL_COMPOSE_SCOPE = "https://www.googleapis.com/auth/gmail.compose"
# What every Calendar tool needs (and all get_credentials requires: older
# tokens have only this, and must keep working).
CALENDAR_SCOPES = [CALENDAR_SCOPE]
# Read-only extras: the calendar list (other calendars, list_calendars) and
# calendar settings (Google's named event labels). Optional: without them
# the core tools still work, and these say to sign in again.
CALENDAR_EXTRA_SCOPES = [
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
    "https://www.googleapis.com/auth/calendar.calendars.readonly"]
GMAIL_SCOPES = [GMAIL_READ_SCOPE, GMAIL_COMPOSE_SCOPE]
# everything, when Gmail is enabled
SCOPES = CALENDAR_SCOPES + CALENDAR_EXTRA_SCOPES + GMAIL_SCOPES


def sign_in_scopes(gmail_enabled: bool = False) -> list[str]:
    """What sign-in asks for: Gmail access only if [gmail] is enabled, so a
    Calendar-only setup never grants mail access."""
    return (CALENDAR_SCOPES + CALENDAR_EXTRA_SCOPES
            + (GMAIL_SCOPES if gmail_enabled else []))
# On a server the OAuth files come from the environment instead (no files
# to upload, nothing written back): the contents of token.json and of
# credentials.json, pasted into the host's secret settings.
TOKEN_ENV = "GOOGLE_TOKEN_JSON"
CLIENT_ENV = "GOOGLE_CLIENT_JSON"
# The "Web application" OAuth client family members sign in with from the
# phone (family.py; Kyle's own GOOGLE_CLIENT_JSON is usually a Desktop one).
WEB_CLIENT_ENV = "GOOGLE_WEB_CLIENT_JSON"
# The public beta's Web client (family.py tier "beta"), in its own Google
# Cloud project whose consent screen lists only the calendar scopes.
PUBLIC_CLIENT_ENV = "GOOGLE_PUBLIC_CLIENT_JSON"
SIGN_IN_COMMAND = "python -m kwil.tools.google_auth"
HEADLESS_REDIRECT = "http://localhost:8765/"


_write_lock = threading.Lock()


def write_private(path: str | Path, text: str) -> None:
    """Write a secret file readable only by its owner (0600 on Linux/Pi),
    atomically: a temp file, then a rename, so concurrent Calendar and
    Gmail refreshes never leave a half-written token."""
    import tempfile

    path = Path(path)
    with _write_lock:  # Windows can't replace a file another thread is replacing
        fd, tmp = tempfile.mkstemp(dir=path.parent or ".", prefix=f".{path.name}.",
                                   suffix=".tmp")  # unique per call, mode 0600
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.chmod(tmp, 0o600)
            for attempt in range(20):  # another process (or a reader) on Windows
                try:
                    os.replace(tmp, path)
                    break
                except PermissionError:
                    if attempt == 19:
                        raise
                    time.sleep(0.05)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


def token_json(creds) -> str:
    """The credentials as JSON, recording the scopes Google actually
    granted (a box left unticked on the consent screen means fewer)."""
    info = json.loads(creds.to_json())
    granted = getattr(creds, "granted_scopes", None)
    if isinstance(granted, (list, tuple, set, frozenset)) and granted:
        info["scopes"] = sorted(granted)
    return json.dumps(info)


def _relax_scope_check() -> None:
    # Without this, oauthlib aborts the whole sign-in ("Scope has changed")
    # when one permission is unticked; we record what was granted instead.
    os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")


class GoogleSignInError(RuntimeError):
    """Google refused to refresh the sign-in (revoked, or expired: in the
    consent screen's Testing mode refresh tokens last only 7 days).
    `reason`: Google's error code (e.g. invalid_grant) when it refused."""

    reason = ""


REFRESH_FAILED = ("Google sign-in could not be refreshed (%s). Sign in "
                  "again on your PC (python -m kwil.tools.google_auth) and "
                  "update token.json, or the GOOGLE_TOKEN_JSON secret on the "
                  "server. If this happens every week, publish the app on the "
                  "Google OAuth consent screen (README).")


def _json_env(name: str, environ) -> dict | None:
    """Parsed JSON from an env var; errors never include the value."""
    raw = environ.get(name, "").strip()
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        raise ValueError(f"{name} is set but is not valid JSON (paste the whole "
                         "file contents)") from None
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object (the whole file)")
    return value


def client_config(credentials_path="credentials.json", environ=os.environ) -> dict | None:
    """The OAuth client (credentials.json) from GOOGLE_CLIENT_JSON or the file."""
    config = _json_env(CLIENT_ENV, environ)
    if config is None and credentials_path and Path(credentials_path).is_file():
        config = json.loads(Path(credentials_path).read_text(encoding="utf-8"))
    return config


def web_client_config(path: str | Path = "", environ=os.environ,
                      env: str = WEB_CLIENT_ENV) -> dict | None:
    """The family sign-in's Web application client: GOOGLE_WEB_CLIENT_JSON
    (or `env`, e.g. GOOGLE_PUBLIC_CLIENT_JSON), else the file at `path`
    ("" = none)."""
    config = _json_env(env, environ)
    if config is None and path and Path(path).is_file():
        config = json.loads(Path(path).read_text(encoding="utf-8"))
    return config


def token_info(token_path="token.json", credentials_path="credentials.json",
               environ=os.environ) -> tuple[dict | None, bool]:
    """(authorized-user info, came from the environment).

    A token without client_id/client_secret (e.g. trimmed by hand) gets them
    from the client config, which refreshing needs."""
    info = _json_env(TOKEN_ENV, environ)
    from_env = info is not None
    if info is None and Path(token_path).exists():
        info = json.loads(Path(token_path).read_text(encoding="utf-8"))
    if info is not None and not (info.get("client_id") and info.get("client_secret")):
        client = client_config(credentials_path, environ) or {}
        client = client.get("installed") or client.get("web") or {}
        for key in ("client_id", "client_secret"):
            if client.get(key):
                info.setdefault(key, client[key])
    return info, from_env


def owner_connected(config: dict) -> bool:
    """Has the owner connected Google in the cloud server's app ([webapp]
    owner_google, google_web.py)? Then that token, in [calendar] token,
    wins over GOOGLE_TOKEN_JSON. An unreadable file counts as not
    connected, so the environment's token keeps working."""
    if not config["webapp"].get("owner_google"):
        return False
    try:  # environ={}: the file only
        info = load_token_info(config["calendar"]["token"], environ={})
    except (OSError, ValueError, AttributeError, TypeError):  # e.g. a file holding []
        return False
    return isinstance(info, dict) and bool(info.get("refresh_token") or info.get("token"))


def owner_environ(config: dict, environ=os.environ):
    """Where the owner's Google sign-in is read from: `environ` without
    GOOGLE_TOKEN_JSON once the owner has connected in the app
    (owner_connected), else `environ` itself."""
    if not owner_connected(config):
        return environ
    return {k: v for k, v in environ.items() if k != TOKEN_ENV}


def has_credentials(credentials_path="credentials.json", token_path="token.json",
                    environ=os.environ) -> bool:
    return bool(environ.get(TOKEN_ENV, "").strip() or environ.get(CLIENT_ENV, "").strip()
                or Path(credentials_path).exists() or Path(token_path).exists())


# Whose Google sign-in Google refused to refresh (revoked, or expired), so
# the app shows "Reconnect" (google_web.py status) instead of "connected":
# OWNER (None) for the owner, else a family member's id, and which token:
# "env" (GOOGLE_TOKEN_JSON) or "file" (a token file). Kept apart, so the
# owner's Disconnect of a file connected in the app, which falls back to
# GOOGLE_TOKEN_JSON, still shows that one's refusal. In memory only, per
# process, and only who, which and when: never a token or Google's
# message. Set by a failed refresh, cleared by a later successful one of
# the same token, a connect or a disconnect. Bounded: the oldest go first.
OWNER = None
SOURCES = ("env", "file")
MAX_RECONNECT = 1024
_reconnect: OrderedDict = OrderedDict()  # (account, source) -> when
_reconnect_lock = threading.Lock()


def _who(account: str | None, source: str) -> str:
    return f"{account or 'the owner'} ({TOKEN_ENV if source == 'env' else 'token file'})"


def mark_refresh_failed(account: str | None = OWNER, source: str = "file") -> None:
    """Remember that `account`'s `source` token must be signed in again."""
    key = (account, source)
    with _reconnect_lock:
        new = key not in _reconnect
        _reconnect.pop(key, None)
        _reconnect[key] = time.time()
        while len(_reconnect) > MAX_RECONNECT:
            _reconnect.popitem(last=False)
    if new:
        log.info("Google sign-in needs reconnecting for %s", _who(account, source))


def clear_reconnect(account: str | None = OWNER, source: str | None = None) -> None:
    """`account`'s `source` token works again (refreshed, connected or
    removed); `source` None: both."""
    with _reconnect_lock:
        gone = [s for s in SOURCES if (source is None or s == source)
                and _reconnect.pop((account, s), None) is not None]
    for s in gone:
        log.info("Google sign-in works again for %s", _who(account, s))


def needs_reconnect(account: str | None = OWNER, source: str | None = None) -> bool:
    """Was `account`'s `source` token refused (None: either)?"""
    with _reconnect_lock:
        return any((account, s) in _reconnect for s in SOURCES
                   if source is None or s == source)


# A family member's token (family.py): they fix it themselves in the app,
# so their errors say that, never Kyle's PC sign-in or GOOGLE_TOKEN_JSON.
REFRESH_FAILED_IN_APP = ("Google sign-in of an in-app account could not be "
                         "refreshed (%s); its owner must reconnect Google in the app.")


def reconnect_hint() -> str:
    """What a family member should do, in the turn's language (i18n.using)."""
    from kwil import i18n

    return i18n.t("google_reconnect")


def _warn_on_refresh_failure(creds, in_app: bool = False,
                             account: str | None = OWNER, source: str = "file"):
    """Make every later refresh (google-auth refreshes inside API calls)
    log a clear warning, with the error code only, if Google refuses, and
    record whether `account` (OWNER, or a family member's id) must
    reconnect (needs_reconnect; `source`: "env" for GOOGLE_TOKEN_JSON, else
    "file"). `in_app`: the token was connected in the app (reconnect
    there)."""
    from google.auth.exceptions import RefreshError

    original = creds.refresh

    def refresh(request):
        try:
            original(request)
        except RefreshError as e:
            reason = _refresh_reason(e)
            if not getattr(e, "retryable", False):  # Google's side, not the grant
                mark_refresh_failed(account, source)
            if in_app:
                log.warning(REFRESH_FAILED_IN_APP, reason)
                error = GoogleSignInError(
                    f"Google sign-in expired or was revoked ({reason}). "
                    f"{reconnect_hint()}")
            else:
                log.warning(REFRESH_FAILED, reason)
                error = GoogleSignInError(
                    f"Google sign-in expired or was revoked ({reason}); "
                    "Kyle needs to sign in again.")
            error.reason = reason
            raise error from None
        clear_reconnect(account, source)

    creds.refresh = refresh
    return creds


def _refresh_reason(error) -> str:
    """A short, secret-free reason: the OAuth error code, e.g. invalid_grant."""
    import re

    text = str(error.args[0]) if error.args else ""
    m = re.match(r"\s*([a-z_]+)\b", text)
    return m.group(1) if m else type(error).__name__


class MissingScopes(RuntimeError):
    """The saved token was granted fewer scopes than a tool needs."""

    def __init__(self, missing: list[str], in_app: bool = False):
        self.missing = missing
        names = ", ".join(s.rsplit("/", 1)[-1] for s in missing)
        if in_app:  # a family member: allow everything when reconnecting
            super().__init__(f"Google access for {names} hasn't been granted. "
                             f"{reconnect_hint()}")
            return
        super().__init__(
            f"Google access for {names} hasn't been granted yet. Run the "
            f"one-time Google sign-in again on the computer: {SIGN_IN_COMMAND} "
            f"(add --headless without a browser), then restart the assistant.")


def load_token_info(token_path: str | Path = "token.json",
                    environ=os.environ) -> dict | None:
    """The saved token as a dict: GOOGLE_TOKEN_JSON first, then the file."""
    return token_info(token_path, environ=environ)[0]


def granted_scopes(info: dict | None) -> set[str]:
    if not info:
        return set()
    scopes = info.get("scopes")
    if scopes is None:  # tokens from before scopes were saved: Calendar only
        return set(CALENDAR_SCOPES)
    return set(scopes.split() if isinstance(scopes, str) else scopes)


def missing_scopes(needed, token_path: str | Path = "token.json",
                   environ=os.environ) -> list[str]:
    """Scopes in `needed` that the saved token lacks (all, if no token)."""
    have = granted_scopes(load_token_info(token_path, environ))
    return [s for s in needed if s not in have]


def get_credentials(credentials_path="credentials.json", token_path="token.json",
                    open_browser: bool = True, save_token: bool = True,
                    environ=os.environ, scopes=CALENDAR_SCOPES,
                    in_app: bool = False, account: str | None = OWNER):
    """Credentials covering `scopes`, refreshed as needed.

    GOOGLE_TOKEN_JSON / GOOGLE_CLIENT_JSON, when set, replace token.json /
    credentials.json. A token from the environment is refreshed in memory
    only and never written to disk; nor is any token if `save_token` is off
    (the server profile), and there is no interactive sign-in then. On a PC
    with no token at all, the first run opens a browser to sign in (for
    `scopes`). A token lacking one of `scopes` raises MissingScopes instead
    of silently signing in again. `in_app`: a family member's token
    (family.py), whose errors say to reconnect Google in the app.
    `account`: whose sign-in it is (OWNER, or a family member's id), for
    needs_reconnect."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    info, from_env = token_info(token_path, credentials_path, environ)
    persist = save_token and not from_env
    source = "env" if from_env else "file"
    creds = None
    if info:
        lacking = [s for s in scopes if s not in granted_scopes(info)]
        if lacking:
            raise MissingScopes(lacking, in_app)
        try:
            # With the scopes the token was granted: a refresh never asks
            # for more than Kyle agreed to.
            creds = Credentials.from_authorized_user_info(
                info, None if info.get("scopes") else CALENDAR_SCOPES)
        except ValueError as e:  # names the missing fields, not values
            mark_refresh_failed(account, source)
            raise GoogleSignInError(f"Unusable Google token "
                                    f"({TOKEN_ENV if from_env else token_path}): "
                                    f"{e}") from None
        _warn_on_refresh_failure(creds, in_app, account, source)
    if creds and creds.valid:
        return creds
    if creds and creds.refresh_token:
        creds.refresh(Request())  # GoogleSignInError (logged) if refused
    elif in_app:
        if info:  # a token without a refresh token, expired
            mark_refresh_failed(account, source)
        raise GoogleSignInError(f"No usable Google sign-in. {reconnect_hint()}")
    elif from_env or not save_token:
        if info:  # a token without a refresh token, expired
            mark_refresh_failed(account, source)
        raise GoogleSignInError(
            f"No usable Google sign-in: set {TOKEN_ENV} to the contents of a "
            f"token.json made on your PC ({SIGN_IN_COMMAND})")
    else:
        creds = _warn_on_refresh_failure(sign_in(
            credentials_path, token_path, open_browser,
            scopes=sorted(set(CALENDAR_SCOPES) | set(scopes)), environ=environ),
            account=account)
        return creds
    if persist:
        write_private(token_path, creds.to_json())
    return creds


CHECK_TIMEOUT = 10  # seconds for verify_sign_in's one call to Google


def verify_sign_in(credentials_path="credentials.json", token_path="token.json",
                   environ=os.environ, in_app: bool = False,
                   account: str | None = OWNER) -> str | None:
    """Ask Google once whether the saved sign-in still works (python -m
    kwil doctor; one token refresh, at most CHECK_TIMEOUT seconds, nothing
    written). None if it does, else what is wrong in plain words, with
    Google's error code only."""
    from google.auth.transport.requests import Request

    request = Request()

    def bounded(*args, **kwargs):
        kwargs.setdefault("timeout", CHECK_TIMEOUT)
        return request(*args, **kwargs)

    try:
        creds = get_credentials(credentials_path, token_path, save_token=False,
                                environ=environ, scopes=(), in_app=in_app,
                                account=account)
        if creds.refresh_token:  # still valid or not: really ask Google
            creds.refresh(bounded)
    except GoogleSignInError as e:
        if e.reason:
            return (f"Google refused it ({e.reason}): the sign-in expired or "
                    "was revoked, so sign in to Google again")
        return str(e)
    except Exception as e:  # e.g. offline: say so, without the message
        return f"couldn't check with Google ({type(e).__name__})"
    return None


def sign_in(credentials_path="credentials.json", token_path="token.json",
            open_browser: bool = True, scopes=CALENDAR_SCOPES, environ=os.environ):
    """Interactive sign-in for `scopes`; saves token.json."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    client = client_config(credentials_path, environ)
    if client is None:
        raise FileNotFoundError(
            f"{credentials_path} not found. Create an OAuth client "
            "(Desktop app) in Google Cloud Console and download it.")
    _relax_scope_check()
    flow = InstalledAppFlow.from_client_config(client, list(scopes))
    creds = flow.run_local_server(port=0, open_browser=open_browser,
                                  prompt="consent")
    write_private(token_path, token_json(creds))
    return creds


def headless_start(credentials_path="credentials.json",
                   state_path=".oauth_state.json", scopes=CALENDAR_SCOPES) -> str:
    """Step 1 of sign-in without a local browser (Raspberry Pi over SSH, a
    cloud box): return the URL to open on any device. The PKCE verifier and
    state are saved so step 2 can run in a separate process."""
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_secrets_file(credentials_path, list(scopes),
                                         redirect_uri=HEADLESS_REDIRECT)
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    write_private(state_path, json.dumps(
        {"state": state, "code_verifier": flow.code_verifier,
         "scopes": list(scopes)}))
    return url


def headless_finish(redirected_url: str, credentials_path="credentials.json",
                    token_path="token.json", state_path=".oauth_state.json"):
    """Step 2: after signing in, the browser lands on a localhost page that
    fails to load. Its full address (with ?code=...) is `redirected_url`."""
    from urllib.parse import parse_qs, urlparse

    from google_auth_oauthlib.flow import Flow

    saved = json.loads(Path(state_path).read_text())
    query = parse_qs(urlparse(redirected_url.strip()).query)
    if "error" in query:
        raise RuntimeError(f"Google sign-in failed: {query['error'][0]}")
    if query.get("state", [None])[0] != saved["state"]:
        raise RuntimeError("State mismatch: use the URL from the latest sign-in link")
    flow = Flow.from_client_secrets_file(credentials_path,
                                         saved.get("scopes", CALENDAR_SCOPES),
                                         redirect_uri=HEADLESS_REDIRECT,
                                         state=saved["state"],
                                         code_verifier=saved["code_verifier"])
    _relax_scope_check()
    flow.fetch_token(code=query["code"][0])
    write_private(token_path, token_json(flow.credentials))
    Path(state_path).unlink(missing_ok=True)
    return flow.credentials


def main(prog: str = "python -m kwil.tools.google_auth") -> None:
    import argparse

    from dotenv import load_dotenv

    from kwil.config import load_config

    load_dotenv()
    parser = argparse.ArgumentParser(
        prog=prog, description="Sign in to Google (Calendar, plus Gmail if "
                               "[gmail] is enabled in config.toml)")
    parser.add_argument("--headless", action="store_true",
                        help="no local browser: print a link, then paste back "
                             "the localhost URL you land on")
    parser.add_argument("--start", action="store_true",
                        help="headless step 1 only: print the link and exit")
    parser.add_argument("--finish", metavar="URL",
                        help="headless step 2 only: the localhost URL")
    args = parser.parse_args()
    config = load_config()
    gmail = bool(config["gmail"]["enabled"])
    scopes = sign_in_scopes(gmail)
    creds = config["calendar"]["credentials"]
    token = config["calendar"]["token"]
    if args.finish:
        headless_finish(args.finish, creds, token)
    elif args.headless or args.start:
        print("Open this link on any device and sign in:\n\n"
              + headless_start(creds, scopes=scopes) + "\n\nAfterwards the browser shows "
              "'This site can't be reached'. That's expected: copy the full "
              "address from the address bar.")
        if args.start:
            raise SystemExit(0)
        headless_finish(input("\nPaste that address here: "), creds, token)
    else:
        sign_in(creds, token, scopes=scopes)
    print(("Google Calendar (events, plus read-only calendar list and labels) "
           "and Gmail (read, drafts) authorized"
           if gmail else "Google Calendar authorized ([gmail] is disabled, so "
           "no mail access was requested)") + "; token saved to", token)
    print(f"For cloud hosting, put the contents of {token} in {TOKEN_ENV}.")


if __name__ == "__main__":
    main()
