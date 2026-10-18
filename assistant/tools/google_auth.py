"""Google sign-in shared by Calendar and Gmail (one OAuth client, one token).

The one-time sign-in asks for every scope the assistant uses. A token saved
before Gmail support only has the Calendar scope: Calendar keeps working
with it, and the Gmail tools say exactly what to re-run
(`python -m assistant.tools.google_auth`).

In cloud mode there is no token file: the token JSON comes from the
GOOGLE_TOKEN_JSON environment variable (refreshed tokens stay in memory).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"
GMAIL_READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
# Drafts only. The assistant never asks for gmail.send.
GMAIL_COMPOSE_SCOPE = "https://www.googleapis.com/auth/gmail.compose"
CALENDAR_SCOPES = [CALENDAR_SCOPE]
GMAIL_SCOPES = [GMAIL_READ_SCOPE, GMAIL_COMPOSE_SCOPE]
SCOPES = CALENDAR_SCOPES + GMAIL_SCOPES  # what sign-in asks for
TOKEN_ENV = "GOOGLE_TOKEN_JSON"
SIGN_IN_COMMAND = "python -m assistant.tools.google_auth"
HEADLESS_REDIRECT = "http://localhost:8765/"


class MissingScopes(RuntimeError):
    """The saved token was granted fewer scopes than a tool needs."""

    def __init__(self, missing: list[str]):
        self.missing = missing
        names = ", ".join(s.rsplit("/", 1)[-1] for s in missing)
        super().__init__(
            f"Google access for {names} hasn't been granted yet. Run the "
            f"one-time Google sign-in again on the computer: {SIGN_IN_COMMAND} "
            f"(add --headless without a browser), then restart the assistant.")


def load_token_info(token_path: str | Path = "token.json",
                    environ=os.environ) -> dict | None:
    """The saved token as a dict: GOOGLE_TOKEN_JSON first, then the file."""
    raw = environ.get(TOKEN_ENV, "").strip()
    if raw:
        return json.loads(raw)
    if Path(token_path).exists():
        return json.loads(Path(token_path).read_text())
    return None


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
                    open_browser: bool = True, scopes=CALENDAR_SCOPES,
                    environ=os.environ):
    """Credentials covering `scopes`, refreshed as needed.

    With no token at all, the first run opens a browser to sign in (asking
    for every scope in SCOPES) and saves token.json. A token that lacks one
    of `scopes` raises MissingScopes instead of silently signing in again."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    info = load_token_info(token_path, environ)
    from_env = bool(environ.get(TOKEN_ENV, "").strip())
    if info is None:
        return sign_in(credentials_path, token_path, open_browser)
    lacking = [s for s in scopes if s not in granted_scopes(info)]
    if lacking:
        raise MissingScopes(lacking)
    # Load with the scopes the token was granted, so a refresh never asks
    # for more than the user agreed to.
    creds = Credentials.from_authorized_user_info(info)
    if creds.valid:
        return creds
    if not creds.refresh_token:
        raise RuntimeError(f"The Google token can't be refreshed. Run "
                           f"{SIGN_IN_COMMAND} again.")
    creds.refresh(Request())
    if not from_env:
        Path(token_path).write_text(creds.to_json())
    return creds


def sign_in(credentials_path="credentials.json", token_path="token.json",
            open_browser: bool = True):
    """Interactive sign-in for every scope; saves token.json."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not Path(credentials_path).exists():
        raise FileNotFoundError(
            f"{credentials_path} not found. Create an OAuth client "
            "(Desktop app) in Google Cloud Console and download it.")
    flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
    creds = flow.run_local_server(port=0, open_browser=open_browser,
                                  prompt="consent")
    Path(token_path).write_text(creds.to_json())
    return creds


def headless_start(credentials_path="credentials.json",
                   state_path=".oauth_state.json") -> str:
    """Step 1 of sign-in without a local browser (Raspberry Pi over SSH, a
    cloud box): return the URL to open on any device. The PKCE verifier and
    state are saved so step 2 can run in a separate process."""
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_secrets_file(credentials_path, SCOPES,
                                         redirect_uri=HEADLESS_REDIRECT)
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    Path(state_path).write_text(json.dumps(
        {"state": state, "code_verifier": flow.code_verifier}))
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
    flow = Flow.from_client_secrets_file(credentials_path, SCOPES,
                                         redirect_uri=HEADLESS_REDIRECT,
                                         state=saved["state"],
                                         code_verifier=saved["code_verifier"])
    flow.fetch_token(code=query["code"][0])
    Path(token_path).write_text(flow.credentials.to_json())
    Path(state_path).unlink(missing_ok=True)
    return flow.credentials


def main(prog: str = "python -m assistant.tools.google_auth") -> None:
    import argparse

    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser(
        prog=prog, description="Sign in to Google (Calendar and Gmail drafts)")
    parser.add_argument("--headless", action="store_true",
                        help="no local browser: print a link, then paste back "
                             "the localhost URL you land on")
    parser.add_argument("--start", action="store_true",
                        help="headless step 1 only: print the link and exit")
    parser.add_argument("--finish", metavar="URL",
                        help="headless step 2 only: the localhost URL")
    args = parser.parse_args()
    creds = os.environ.get("GOOGLE_CREDENTIALS", "credentials.json")
    token = os.environ.get("GOOGLE_TOKEN", "token.json")
    if args.finish:
        headless_finish(args.finish, creds, token)
    elif args.headless or args.start:
        print("Open this link on any device and sign in:\n\n"
              + headless_start(creds) + "\n\nAfterwards the browser shows "
              "'This site can't be reached'. That's expected: copy the full "
              "address from the address bar.")
        if args.start:
            raise SystemExit(0)
        headless_finish(input("\nPaste that address here: "), creds, token)
    else:
        sign_in(creds, token)
    print("Google Calendar and Gmail (read + drafts) authorized; token saved to",
          token)
    print(f"For cloud hosting, put the contents of {token} in {TOKEN_ENV}.")


if __name__ == "__main__":
    main()
