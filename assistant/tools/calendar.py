"""Google Calendar tools: list, create, move, delete events."""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, time, timedelta
from pathlib import Path

from assistant.tools import Tool
from assistant.tools.timeutil import (local_zone, parse_datetime, resolve_day,
                                      spoken_date, spoken_time, zone_name)

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


# On a server the OAuth files come from the environment instead (no files
# to upload, nothing written back): the contents of token.json and of
# credentials.json, pasted into the host's secret settings.
TOKEN_ENV = "GOOGLE_TOKEN_JSON"
CLIENT_ENV = "GOOGLE_CLIENT_JSON"


class GoogleSignInError(RuntimeError):
    """Google refused to refresh the sign-in (revoked, or expired: in the
    consent screen's Testing mode refresh tokens last only 7 days)."""


REFRESH_FAILED = ("Google Calendar sign-in could not be refreshed (%s). Sign in "
                  "again on your PC (python -m assistant.tools.calendar) and "
                  "update token.json, or the GOOGLE_TOKEN_JSON secret on the "
                  "server. If this happens every week, publish the app on the "
                  "Google OAuth consent screen (README).")


def _json_env(name: str, environ) -> dict | None:
    """Parsed JSON from an env var; errors never include the value."""
    import json

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
    import json

    config = _json_env(CLIENT_ENV, environ)
    if config is None and Path(credentials_path).exists():
        config = json.loads(Path(credentials_path).read_text(encoding="utf-8"))
    return config


def token_info(token_path="token.json", credentials_path="credentials.json",
               environ=os.environ) -> tuple[dict | None, bool]:
    """(authorized-user info, came from the environment).

    A token without client_id/client_secret (e.g. trimmed by hand) gets them
    from the client config, which refreshing needs."""
    import json

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


def has_credentials(credentials_path="credentials.json", token_path="token.json",
                    environ=os.environ) -> bool:
    return bool(environ.get(TOKEN_ENV, "").strip() or environ.get(CLIENT_ENV, "").strip()
                or Path(credentials_path).exists() or Path(token_path).exists())


def _warn_on_refresh_failure(creds):
    """Make every later refresh (google-auth refreshes inside API calls)
    log a clear warning, with the error code only, if Google refuses."""
    from google.auth.exceptions import RefreshError

    original = creds.refresh

    def refresh(request):
        try:
            original(request)
        except RefreshError as e:
            reason = _refresh_reason(e)
            log.warning(REFRESH_FAILED, reason)
            raise GoogleSignInError(
                f"Google sign-in expired or was revoked ({reason}); "
                "Kyle needs to sign in again.") from None

    creds.refresh = refresh
    return creds


def _refresh_reason(error) -> str:
    """A short, secret-free reason: the OAuth error code, e.g. invalid_grant."""
    import re

    text = str(error.args[0]) if error.args else ""
    m = re.match(r"\s*([a-z_]+)\b", text)
    return m.group(1) if m else type(error).__name__


def get_credentials(credentials_path="credentials.json", token_path="token.json",
                    open_browser: bool = True, save_token: bool = True,
                    environ=os.environ):
    """OAuth for an installed app. The first run opens a browser to sign in
    and saves token.json; later runs refresh it silently.

    GOOGLE_TOKEN_JSON / GOOGLE_CLIENT_JSON, when set, replace token.json /
    credentials.json. A token from the environment is refreshed in memory
    only and never written to disk; nor is any token if `save_token` is off
    (the server profile). There is no interactive sign-in then: a server
    has no browser."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    info, from_env = token_info(token_path, credentials_path, environ)
    persist = save_token and not from_env
    creds = None
    if info:
        try:
            creds = Credentials.from_authorized_user_info(info, SCOPES)
        except ValueError as e:  # names the missing fields, not values
            raise GoogleSignInError(f"Unusable Google token "
                                    f"({TOKEN_ENV if from_env else token_path}): "
                                    f"{e}") from None
    if creds is not None:
        _warn_on_refresh_failure(creds)
    if creds and creds.valid:
        return creds
    if creds and creds.refresh_token:
        creds.refresh(Request())  # GoogleSignInError (logged) if refused
    elif from_env or not save_token:
        raise GoogleSignInError(
            f"No usable Google sign-in: set {TOKEN_ENV} to the contents of a "
            "token.json made on your PC (python -m assistant.tools.calendar)")
    else:
        from google_auth_oauthlib.flow import InstalledAppFlow

        client = client_config(credentials_path, environ)
        if client is None:
            raise FileNotFoundError(
                f"{credentials_path} not found. Create an OAuth client "
                "(Desktop app) in Google Cloud Console and download it.")
        flow = InstalledAppFlow.from_client_config(client, SCOPES)
        creds = _warn_on_refresh_failure(
            flow.run_local_server(port=0, open_browser=open_browser))
    if persist:
        Path(token_path).write_text(creds.to_json())
    return creds


HEADLESS_REDIRECT = "http://localhost:8765/"


def headless_start(credentials_path="credentials.json",
                   state_path=".oauth_state.json") -> str:
    """Step 1 of sign-in without a local browser (Raspberry Pi over SSH, a
    cloud box): return the URL to open on any device. The PKCE verifier and
    state are saved so step 2 can run in a separate process."""
    import json

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
    import json
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


def _is_date(value: str) -> bool:
    """True for a bare YYYY-MM-DD (an all-day value), not a date-time."""
    try:
        date.fromisoformat(value.strip())
        return len(value.strip()) == 10
    except ValueError:
        return False


class GoogleCalendar:
    def __init__(self, service=None, credentials_path="credentials.json",
                 token_path="token.json", calendar_id="primary",
                 timezone: str | None = None, save_token: bool = True):
        self._service = service
        self.credentials_path = credentials_path
        self.token_path = token_path
        self.save_token = save_token
        self.calendar_id = calendar_id
        self.tz = local_zone(timezone)

    @property
    def service(self):
        if self._service is None:
            from googleapiclient.discovery import build

            creds = get_credentials(self.credentials_path, self.token_path,
                                    save_token=self.save_token)
            self._service = build("calendar", "v3", credentials=creds,
                                  cache_discovery=False)
        return self._service

    # ----- helpers -----
    def _event_times(self, event) -> tuple[datetime | date, datetime | date]:
        s, e = event["start"], event["end"]
        if "dateTime" in s:
            return (parse_datetime(s["dateTime"], self.tz),
                    parse_datetime(e["dateTime"], self.tz))
        return date.fromisoformat(s["date"]), date.fromisoformat(e["date"])

    def _summarize(self, event) -> dict:
        start, end = self._event_times(event)
        info = {"id": event["id"], "title": event.get("summary", "(no title)")}
        if isinstance(start, datetime):
            info.update(date=spoken_date(start.date()), start=spoken_time(start),
                        end=spoken_time(end))
        else:
            info.update(date=spoken_date(start), all_day=True)
        if event.get("location"):
            info["location"] = event["location"]
        return info

    def _when(self, dt: datetime) -> dict:
        when = {"dateTime": dt.isoformat()}
        if zone_name(self.tz):
            when["timeZone"] = zone_name(self.tz)
        return when

    # ----- tools -----
    def list_events(self, day: str = "today", days: int = 1) -> dict:
        first = resolve_day(day, self.tz)
        start = datetime.combine(first, time.min, self.tz)
        end = start + timedelta(days=max(1, days))
        result = self.service.events().list(
            calendarId=self.calendar_id, timeMin=start.isoformat(),
            timeMax=end.isoformat(), singleEvents=True, orderBy="startTime",
            maxResults=50).execute()
        events = [self._summarize(e) for e in result.get("items", [])]
        return {"range": spoken_date(first) + (f" plus {days - 1} more days"
                                               if days > 1 else ""),
                "count": len(events), "events": events}

    def create_event(self, title: str, start: str, end: str | None = None,
                     duration_minutes: int = 60, location: str | None = None,
                     description: str | None = None) -> dict:
        if _is_date(start):  # all-day; `end` is the last day, inclusive
            start_d = date.fromisoformat(start.strip())
            end_d = date.fromisoformat(end.strip()[:10]) if end else start_d
            if end_d < start_d:
                raise ValueError("end is before start")
            body = {"summary": title, "start": {"date": start_d.isoformat()},
                    "end": {"date": (end_d + timedelta(days=1)).isoformat()}}
        else:
            start_dt = parse_datetime(start, self.tz)
            end_dt = (parse_datetime(end, self.tz) if end
                      else start_dt + timedelta(minutes=duration_minutes))
            body = {"summary": title, "start": self._when(start_dt),
                    "end": self._when(end_dt)}
        if location:
            body["location"] = location
        if description:
            body["description"] = description
        event = self.service.events().insert(calendarId=self.calendar_id,
                                             body=body).execute()
        return {"status": "created", "event": self._summarize(event)}

    def move_event(self, event_id: str, new_start: str,
                   new_end: str | None = None) -> dict:
        event = self.service.events().get(calendarId=self.calendar_id,
                                          eventId=event_id).execute()
        old_start, old_end = self._event_times(event)
        if isinstance(old_start, datetime):
            start_dt = parse_datetime(new_start, self.tz)
            end_dt = (parse_datetime(new_end, self.tz) if new_end
                      else start_dt + (old_end - old_start))
            patch = {"start": self._when(start_dt), "end": self._when(end_dt)}
        else:  # all-day event: keep it all-day; new_end is the last day
            start_d = date.fromisoformat(new_start[:10])
            end_d = (date.fromisoformat(new_end[:10]) + timedelta(days=1)
                     if new_end else start_d + (old_end - old_start))
            patch = {"start": {"date": start_d.isoformat()},
                     "end": {"date": end_d.isoformat()}}
        event = self.service.events().patch(calendarId=self.calendar_id,
                                            eventId=event_id,
                                            body=patch).execute()
        return {"status": "moved", "event": self._summarize(event)}

    def delete_event(self, event_id: str) -> dict:
        self.service.events().delete(calendarId=self.calendar_id,
                                     eventId=event_id).execute()
        return {"status": "deleted", "event_id": event_id}

    def describe_for_delete(self, tool_input: dict) -> str:
        try:
            event = self.service.events().get(
                calendarId=self.calendar_id,
                eventId=tool_input["event_id"]).execute()
            info = self._summarize(event)
            when = info["date"] + (f" at {info['start']}" if "start" in info else "")
            return f"Do you want me to delete {info['title']} on {when}?"
        except Exception:
            return "Do you want me to delete that event?"

    def _spoken_when(self, iso: str) -> str:
        dt = parse_datetime(iso, self.tz)
        return f"{spoken_date(dt.date())} at {spoken_time(dt)}"

    def describe_for_create(self, tool_input: dict) -> str:
        try:
            when = self._spoken_when(tool_input["start"])
            return (f"Do you want me to add {tool_input['title']} on {when} "
                    "to your calendar?")
        except Exception:
            return "Do you want me to add that event to your calendar?"

    def describe_for_move(self, tool_input: dict) -> str:
        try:
            event = self.service.events().get(
                calendarId=self.calendar_id,
                eventId=tool_input["event_id"]).execute()
            title = event.get("summary", "that event")
            when = self._spoken_when(tool_input["new_start"])
            return f"Do you want me to move {title} to {when}?"
        except Exception:
            return "Do you want me to move that event?"

    def tools(self) -> list[Tool]:
        iso = ("ISO 8601 local date-time without offset, e.g. "
               "2026-10-17T15:00:00")
        return [
            Tool("list_calendar_events",
                 "List events on Kyle's Google Calendar for a day (or several "
                 "days starting then). Returns event ids needed to move or "
                 "delete events.",
                 {"type": "object", "properties": {
                     "day": {"type": "string", "description":
                             "'today', 'tomorrow', YYYY-MM-DD (preferred for "
                             "other days), 'friday' (the coming one, or today "
                             "if it is Friday), or 'next friday' (never today)"},
                     "days": {"type": "integer", "minimum": 1, "maximum": 14,
                              "description": "Number of days to include (default 1)"}},
                  "required": ["day"]},
                 self.list_events),
            Tool("create_calendar_event",
                 "Create an event on Kyle's calendar. For an all-day event "
                 "pass dates only (YYYY-MM-DD) as start and end.",
                 {"type": "object", "properties": {
                     "title": {"type": "string"},
                     "start": {"type": "string", "description":
                               iso + ", or YYYY-MM-DD for an all-day event"},
                     "end": {"type": "string", "description":
                             "Optional. " + iso + ", or for an all-day event "
                             "the last day (YYYY-MM-DD, inclusive)"},
                     "duration_minutes": {"type": "integer", "description":
                                          "Used if end is omitted (default 60)"},
                     "location": {"type": "string"},
                     "description": {"type": "string"}},
                  "required": ["title", "start"]},
                 self.create_event, confirm_if_untrusted=self.describe_for_create),
            Tool("move_calendar_event",
                 "Move an existing event to a new time, keeping its length "
                 "unless new_end is given. Get event_id from "
                 "list_calendar_events first.",
                 {"type": "object", "properties": {
                     "event_id": {"type": "string"},
                     "new_start": {"type": "string", "description": iso},
                     "new_end": {"type": "string", "description":
                                 iso + ". Optional. For all-day events, the "
                                 "last day (YYYY-MM-DD, inclusive)."}},
                  "required": ["event_id", "new_start"]},
                 self.move_event, confirm_if_untrusted=self.describe_for_move),
            Tool("delete_calendar_event",
                 "Delete an event. Kyle is asked to confirm out loud "
                 "automatically; just call this tool, don't ask first yourself. "
                 "Get event_id from list_calendar_events first.",
                 {"type": "object", "properties": {"event_id": {"type": "string"}},
                  "required": ["event_id"]},
                 self.delete_event, confirm_prompt=self.describe_for_delete),
        ]


if __name__ == "__main__":  # python -m assistant.tools.calendar  -> sign in once
    import argparse

    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser(prog="python -m assistant.tools.calendar",
                                     description="Sign in to Google Calendar")
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
        get_credentials(creds, token)
    print("Google Calendar authorized; token saved to", token)
