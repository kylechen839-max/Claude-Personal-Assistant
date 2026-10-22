"""Google Calendar tools: list, search, create, move, update and delete
events (colors, labels, repeats, reminders, guests, Meet), answer invites,
find free time, and list Kyle's calendars."""

from __future__ import annotations

import logging
import os
import re
import time as _time
import uuid
from datetime import date, datetime, time, timedelta, timezone

from kwil import i18n
from kwil.tools import UNTRUSTED_CONTEXT, UNTRUSTED_MARK, Tool, mark_untrusted
# Sign-in lives in google_auth (shared with Gmail); re-exported here so
# `python -m kwil.tools.calendar` and older imports keep working.
from kwil.tools.google_auth import (  # noqa: F401
    CALENDAR_EXTRA_SCOPES, CALENDAR_SCOPES, CLIENT_ENV, HEADLESS_REDIRECT,
    REFRESH_FAILED, SCOPES, TOKEN_ENV, GoogleSignInError, MissingScopes,
    _warn_on_refresh_failure, client_config, get_credentials, has_credentials,
    headless_finish, headless_start, token_info)
from kwil.tools.timeutil import (local_zone, parse_datetime, resolve_day,
                                      spoken_date, spoken_time, zone_name)
from kwil.tools.timeutil import now as turn_now

log = logging.getLogger(__name__)

# Google's 11 classic event colors (colorId -> the name its UI shows). The
# palette is fixed; colors.get would need a wider scope, so it's hard-coded.
CLASSIC_COLORS = {"1": "Lavender", "2": "Sage", "3": "Grape", "4": "Flamingo",
                  "5": "Banana", "6": "Tangerine", "7": "Peacock",
                  "8": "Graphite", "9": "Blueberry", "10": "Basil",
                  "11": "Tomato"}
_COLOR_IDS = {name.lower(): cid for cid, name in CLASSIC_COLORS.items()}
# Everyday words for the classic colors ("make it red").
COLOR_WORDS = {"red": "Tomato", "pink": "Flamingo", "orange": "Tangerine",
               "yellow": "Banana", "green": "Basil", "light green": "Sage",
               "dark green": "Basil", "blue": "Blueberry", "dark blue": "Blueberry",
               "light blue": "Peacock", "teal": "Peacock", "turquoise": "Peacock",
               "cyan": "Peacock", "purple": "Grape", "light purple": "Lavender",
               "violet": "Lavender", "gray": "Graphite", "grey": "Graphite"}
SHOW_AS = {"busy": "opaque", "free": "transparent"}
VISIBILITY = ("default", "public", "private", "confidential")
RESPONSES = {"accepted": "accepted", "accept": "accepted", "yes": "accepted",
             "declined": "declined", "decline": "declined", "no": "declined",
             "tentative": "tentative", "maybe": "tentative"}
SCOPES_ARG = ("this", "all")
REPEAT_PREFIXES = ("RRULE:", "EXDATE:", "EXDATE;", "RDATE:", "RDATE;")
# Bounds on what one call may ask Google for or hand back to Claude.
MAX_ATTENDEES = 20
MAX_REMINDERS = 5
MAX_REMINDER_MINUTES = 40320  # four weeks, Google's limit
MAX_REPEAT_LINES = 5
MAX_REPEAT_LENGTH = 200
MAX_QUERY = 100
MAX_CALENDARS = 10       # list_calendar_events(calendar="all")
MAX_DESCRIPTION = 300    # characters of an event's notes in a listing
MAX_COMMENT = 200        # an RSVP note
MAX_FREE_SLOTS = 30
CACHE_SECONDS = 300      # labels and the calendar list
CACHE_ENTRIES = 20
GOOGLE_PUBLIC_SUFFIX = "@group.v.calendar.google.com"  # holidays, birthdays
MAX_CALENDAR_NAME = 60   # list_calendars: another person's calendar name
# extendedProperties.private key on events whose text the assistant wrote
# in a tainted conversation; never cleared (written_under_taint)
UNTRUSTED_PROPERTY = "hc_untrusted"
_EMAIL = re.compile(r"^[^@\s,;<>\"()]+@[^@\s,;<>\"()]+\.[^@\s,;<>\"()]+$")


# Creating, moving and changing an event aren't destructive: once Kyle has
# said what he wants, the model shouldn't ask "are you sure?" on top of the
# app's own question (asked only when it is needed: guests, untrusted text).
JUST_DO_IT = ("When Kyle asks for this, just call the tool: don't ask him to "
              "confirm first yourself.")


def is_from_others(event: dict) -> bool:
    """Could someone other than Kyle have written this event's title or
    location? The Calendar API sets organizer.self only when it is true, so
    anything else is an invite. Events made from Gmail (eventType
    "fromGmail") and events whose guests may edit them count too; Google's
    birthday events (from Kyle's contacts) don't."""
    if (event.get("organizer") or {}).get("self") is not True:
        return True
    if written_under_taint(event):
        return True
    if event.get("eventType", "default") not in ("default", "focusTime",
                                                 "outOfOffice", "workingLocation",
                                                 "birthday"):
        return True
    return bool(event.get("guestsCanModify"))


def written_under_taint(event: dict) -> bool:
    """Did the assistant write this event's text while untrusted text was
    in the conversation? Then it may carry that text (an email's, a web
    page's) and stays someone else's in every later conversation."""
    private = (event.get("extendedProperties") or {}).get("private") or {}
    return private.get(UNTRUSTED_PROPERTY) == "1"


def _mark_written_under_taint(body: dict, current: dict | None = None) -> None:
    """Add the sticky marker to an insert/patch body. The event's other
    private properties go along, so the patch keeps them either way."""
    private = dict(((current or {}).get("extendedProperties") or {})
                   .get("private") or {})
    private[UNTRUSTED_PROPERTY] = "1"
    body["extendedProperties"] = {"private": private}


def is_google_public(calendar_id: str, entry: dict | None) -> bool:
    """One of Google's own read-only calendars (holidays, contacts'
    birthdays): its text is Google's, not another person's. Their ids end
    in @group.v.calendar.google.com, which nobody else can create."""
    if not str(calendar_id).endswith(GOOGLE_PUBLIC_SUFFIX):
        return False
    return entry is None or entry.get("accessRole") in ("reader", "freeBusyReader")


def _is_date(value: str) -> bool:
    """True for a bare YYYY-MM-DD (an all-day value), not a date-time."""
    try:
        date.fromisoformat(value.strip())
        return len(value.strip()) == 10
    except ValueError:
        return False


def _as_list(value) -> list:
    """A list argument; a lone string is one item, never its characters."""
    if value is None:
        return []
    if isinstance(value, (str, int)):
        return [value]
    return list(value)


def check_emails(emails, limit: int = MAX_ATTENDEES) -> list[str]:
    """Guest addresses: trimmed, valid-looking, no repeats, at most `limit`."""
    out, seen = [], set()
    for n, item in enumerate(_as_list(emails), 1):
        email = str(item).strip()
        if not _EMAIL.match(email) or len(email) > 254:
            # the position only: the value would reach the log with the error
            raise ValueError(f"Guest {n} is not an email address")
        if email.lower() not in seen:
            seen.add(email.lower())
            out.append(email)
    if len(out) > limit:
        raise ValueError(f"At most {limit} guests at a time")
    return out


def check_reminders(reminders) -> dict:
    """Minutes before the event -> Google's reminders. 0 (at the start) is a
    real reminder; [] means none at all."""
    minutes = []
    for item in _as_list(reminders):
        if isinstance(item, bool) or not isinstance(item, (int, str)) \
                or not str(item).strip().isdigit():
            raise ValueError("Reminder minutes must be whole numbers")
        m = int(item)
        if not 0 <= m <= MAX_REMINDER_MINUTES:
            raise ValueError(f"Reminders go from 0 to {MAX_REMINDER_MINUTES} "
                             "minutes (four weeks) before")
        if m not in minutes:
            minutes.append(m)
    if len(minutes) > MAX_REMINDERS:
        raise ValueError(f"At most {MAX_REMINDERS} reminders")
    return {"useDefault": False,
            "overrides": [{"method": "popup", "minutes": m} for m in minutes]}


def check_repeat(repeat) -> list[str]:
    """RRULE/EXDATE/RDATE lines (RFC 5545), bounded."""
    lines = [str(line).strip() for line in _as_list(repeat)]
    if len(lines) > MAX_REPEAT_LINES:
        raise ValueError(f"At most {MAX_REPEAT_LINES} repeat lines")
    for line in lines:
        if (len(line) > MAX_REPEAT_LENGTH or "\n" in line or "\r" in line
                or not line.upper().startswith(REPEAT_PREFIXES)):
            raise ValueError("Each repeat line must be one RRULE:, EXDATE or "
                             f"RDATE line of at most {MAX_REPEAT_LENGTH} "
                             "characters, e.g. RRULE:FREQ=WEEKLY;BYDAY=TU,TH")
    return lines


WEEKDAY_CODES = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
# rule parts that pin dates a shifted start can't carry along
FIXED_DATE_PARTS = {"BYMONTHDAY", "BYYEARDAY", "BYMONTH", "BYWEEKNO", "BYSETPOS"}
CANT_SHIFT = ("That series can't simply move: {why}. Say the new days and "
              "time and I'll make a new series, then delete the old one.")


def follow_new_day(rules: list[str], new_day: date | None) -> list[str]:
    """The repeat lines after the whole series moves; `new_day` is the
    moved occurrence's new date when the move changes the day. A one-weekday
    BYDAY follows it (Google always writes BYDAY on a weekly event); several
    weekdays, an nth weekday or fixed dates can't follow, and EXDATE/RDATE
    dates would stop matching any move: refused, nothing changes."""
    out = []
    for line in rules:
        head, _, body = line.partition(":")
        name = head.split(";")[0].strip().upper()
        if name in ("EXDATE", "RDATE"):
            raise ValueError(CANT_SHIFT.format(
                why="it has skipped or extra dates that wouldn't move with it"))
        if name != "RRULE" or new_day is None:
            out.append(line)
            continue
        parts = [p for p in body.split(";") if p]
        keys = [p.split("=", 1)[0].strip().upper() for p in parts]
        if FIXED_DATE_PARTS & set(keys):
            raise ValueError(CANT_SHIFT.format(
                why="its repeat rule is tied to dates of the month or year"))
        if "BYDAY" in keys:
            i = keys.index("BYDAY")
            value = parts[i].split("=", 1)[1].strip().upper()
            if value not in WEEKDAY_CODES:
                raise ValueError(CANT_SHIFT.format(
                    why="it repeats on several weekdays, or on an nth weekday"))
            parts[i] = "BYDAY=" + WEEKDAY_CODES[new_day.weekday()]
        out.append(head + ":" + ";".join(parts))
    return out


def _choice(value, choices, what: str) -> str:
    text = str(value).strip().lower()
    if text not in choices:
        raise ValueError(f"{what} must be one of: {', '.join(choices)}")
    return text


def _meet_request() -> dict:
    """A new Meet link. The requestId is fresh per call, so a retry of a
    different request never reuses an older conference."""
    return {"createRequest": {"requestId": uuid.uuid4().hex,
                              "conferenceSolutionKey": {"type": "hangoutsMeet"}}}


def _spoken_list(items: list[str]) -> str:
    """Every item, never "and 3 more": a guest question must name each
    address that Google will email (at most MAX_ATTENDEES)."""
    if i18n.is_zh():
        return "、".join(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


class GoogleCalendar:
    def __init__(self, service=None, credentials_path="credentials.json",
                 token_path="token.json", calendar_id="primary",
                 timezone: str | None = None, save_token: bool = True,
                 environ=None, in_app: bool = False,
                 color_labels: dict | None = None, extras: bool = False,
                 clock=_time.monotonic):
        self._service = service
        self.credentials_path = credentials_path
        self.token_path = token_path
        self.save_token = save_token
        # where GOOGLE_TOKEN_JSON / GOOGLE_CLIENT_JSON are read (None: the
        # process environment); a family member's is empty (family.py)
        self.environ = os.environ if environ is None else environ
        self.in_app = in_app  # errors say "reconnect Google in the app"
        self.calendar_id = calendar_id
        self.tz = local_zone(timezone)
        # Kyle's own names for colors ([calendar] color_labels), e.g.
        # {"School": "Basil"}; never a family member's (family.py)
        self.color_labels = {str(k).strip(): str(v).strip()
                             for k, v in (color_labels if isinstance(
                                 color_labels, dict) else {}).items()
                             if str(k).strip() and str(v).strip()}
        # the token has the read-only calendar list + calendar scopes
        # (CALENDAR_EXTRA_SCOPES): labels, other calendars, list_calendars
        self.extras = extras
        self._clock = clock
        self._cache: dict = {}

    @property
    def service(self):
        if self._service is None:
            from googleapiclient.discovery import build

            creds = get_credentials(self.credentials_path, self.token_path,
                                    save_token=self.save_token,
                                    environ=self.environ, in_app=self.in_app)
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

    def _cached(self, key, fetch):
        """`fetch()`, remembered for CACHE_SECONDS (bounded)."""
        now = self._clock()
        hit = self._cache.get(key)
        if hit is not None and now - hit[0] < CACHE_SECONDS:
            return hit[1]
        value = fetch()
        self._cache.pop(key, None)
        self._cache[key] = (now, value)
        while len(self._cache) > CACHE_ENTRIES:
            self._cache.pop(next(iter(self._cache)))
        return value

    def _need_extras(self) -> None:
        if not self.extras:
            raise MissingScopes(CALENDAR_EXTRA_SCOPES, self.in_app)

    def _calendar_list(self) -> list[dict]:
        self._need_extras()
        return self._cached("calendars", lambda: list(
            self.service.calendarList().list(maxResults=250).execute()
            .get("items", [])))

    def _labels(self, calendar_id: str) -> list[dict]:
        """The calendar's named event labels (Google's "event labels");
        none without the extra scope or if Google won't say."""
        if not self.extras:
            return []

        def fetch():
            cal = self.service.calendars().get(calendarId=calendar_id).execute()
            labels = (cal.get("labelProperties") or {}).get("eventLabels") or []
            return [lb for lb in labels if lb.get("id")][:200]
        try:
            return self._cached(("labels", calendar_id), fetch)
        except Exception as e:  # colors still work without the labels
            log.warning("Calendar: event labels unavailable (%s)", type(e).__name__)
            return []

    @staticmethod
    def _calendar_name(entry: dict) -> str:
        return entry.get("summaryOverride") or entry.get("summary") or entry["id"]

    def _resolve_calendar(self, calendar) -> tuple[str, dict | None]:
        """(calendar id, its calendarList entry or None) for an id, a name
        or nothing (the configured calendar)."""
        wanted = str(calendar or "").strip()
        if not wanted or wanted == self.calendar_id:
            return self.calendar_id, None
        if wanted.lower() == "primary":
            return "primary", None
        if not self.extras and "@" in wanted:  # an id; names need the list
            return wanted, None
        for entry in self._calendar_list():
            if wanted == entry.get("id") or wanted.lower() in (
                    str(entry.get("summary", "")).lower(),
                    str(entry.get("summaryOverride", "")).lower()):
                return entry["id"], entry
        if "@" in wanted:  # an id of a calendar not in the list
            return wanted, None
        raise ValueError("No calendar by that name; list_calendars gives the names")

    def _owned(self, calendar_id: str, entry: dict | None) -> bool:
        """Kyle's own calendar? Unknown ones count as someone else's."""
        if entry is not None:
            return entry.get("accessRole") == "owner"
        return calendar_id in ("primary", self.calendar_id)

    def _outside(self, event: dict, calendar_id: str, entry: dict | None) -> bool:
        """Could this event's text, notes included, be someone else's?
        Events Kyle organizes on his own calendars are his, notes and all
        (Kyle's call, 2026-10-22), and so are Google's public calendars."""
        if is_google_public(calendar_id, entry):
            return False
        return not self._owned(calendar_id, entry) or is_from_others(event)

    def color_choices(self, calendar_id: str | None = None) -> dict:
        labels = self._labels(calendar_id or self.calendar_id)
        return {"classic": list(CLASSIC_COLORS.values()),
                "labels": [lb.get("name", "") for lb in labels if lb.get("name")],
                "aliases": dict(self.color_labels)}

    def resolve_color(self, color: str, calendar_id: str | None = None) -> dict:
        """A spoken color -> {"colorId": ...} or {"eventLabelId": ...}.

        Order: Kyle's own aliases ([calendar] color_labels) first, then the
        calendar's label names, Google's classic names, everyday words
        ("red"), and last a bare colorId 1-11."""
        calendar_id = calendar_id or self.calendar_id
        wanted = " ".join(str(color).split()).lower()
        for alias, target in self.color_labels.items():
            if alias.lower() == wanted:
                wanted = " ".join(target.split()).lower()
                break
        for label in self._labels(calendar_id):
            if " ".join(str(label.get("name", "")).split()).lower() == wanted:
                return {"eventLabelId": label["id"]}
        wanted = COLOR_WORDS.get(wanted, wanted).lower()
        if wanted in _COLOR_IDS:
            return {"colorId": _COLOR_IDS[wanted]}
        if wanted in CLASSIC_COLORS:
            return {"colorId": wanted}
        choices = self.color_choices(calendar_id)
        names = (choices["classic"] + choices["labels"]
                 + list(choices["aliases"]))
        raise ValueError("Unknown color. Choices: " + ", ".join(names))

    def _label_name(self, event: dict, calendar_id: str) -> str:
        for label in self._labels(calendar_id):
            if label["id"] == event["eventLabelId"]:
                return label.get("name") or "a label"
        return "a label"

    def _summarize(self, event, calendar_id: str | None = None,
                   notes: bool = False) -> dict:
        start, end = self._event_times(event)
        info = {"id": event["id"], "title": event.get("summary", "(no title)")}
        if isinstance(start, datetime):
            info.update(date=spoken_date(start.date()), start=spoken_time(start),
                        end=spoken_time(end))
        else:
            info.update(date=spoken_date(start), all_day=True)
        if event.get("location"):
            info["location"] = event["location"]
        if event.get("eventLabelId"):  # a label overrides colorId
            info["label"] = self._label_name(event, calendar_id or self.calendar_id)
        elif event.get("colorId") in CLASSIC_COLORS:
            info["color"] = CLASSIC_COLORS[event["colorId"]]
        if event.get("recurringEventId") or event.get("recurrence"):
            info["recurring"] = True
        attendees = event.get("attendees") or []
        me = next((a for a in attendees if a.get("self") is True), None)
        if me is not None and me.get("responseStatus"):
            info["my_response"] = me["responseStatus"]
        guests = sum(1 for a in attendees
                     if a.get("self") is not True and not a.get("resource"))
        if guests:
            info["guests"] = guests
        if event.get("hangoutLink"):
            info["meet"] = event["hangoutLink"]
        elif ((event.get("conferenceData") or {}).get("createRequest") or {}) \
                .get("status", {}).get("statusCode") == "pending":
            info["meet"] = "being set up"
        if event.get("transparency") == "transparent":
            info["show_as"] = "free"
        if notes and event.get("description"):
            text = event["description"]
            info["description"] = (text[:MAX_DESCRIPTION] + "..."
                                   if len(text) > MAX_DESCRIPTION else text)
        if is_from_others(event):
            info["from_others"] = True  # its title/location is someone else's text
        return info

    def _when(self, dt: datetime) -> dict:
        when = {"dateTime": dt.isoformat()}
        if zone_name(self.tz):
            when["timeZone"] = zone_name(self.tz)
        return when

    def _needs_zone(self) -> None:
        if not zone_name(self.tz):
            raise ValueError("Repeating timed events need a named time zone: "
                             "set [assistant] timezone, e.g. America/New_York")

    def _get(self, calendar_id: str, event_id: str) -> dict:
        return self.service.events().get(calendarId=calendar_id,
                                         eventId=event_id).execute()

    def _target(self, calendar_id: str, event_id: str, scope: str):
        """(the event as listed, the one to change): for scope "all" on one
        occurrence of a repeating event, the whole series."""
        scope = _choice(scope or "this", SCOPES_ARG, "scope")
        event = self._get(calendar_id, event_id)
        if scope == "all" and event.get("recurringEventId"):
            return event, self._get(calendar_id, event["recurringEventId"])
        return event, event

    # ----- tools -----
    def list_events(self, day: str = "today", days: int = 1,
                    query: str | None = None, calendar: str | None = None) -> dict:
        first = resolve_day(day, self.tz)
        start = datetime.combine(first, time.min, self.tz)
        end = start + timedelta(days=max(1, days))
        extra = {}
        if query is not None and str(query).strip():
            if len(str(query)) > MAX_QUERY:
                raise ValueError(f"Search words: at most {MAX_QUERY} characters")
            extra["q"] = str(query).strip()
        merged = str(calendar or "").strip().lower() == "all"
        if merged:
            entries = [e for e in self._calendar_list() if not e.get("hidden")]
            entries.sort(key=lambda e: not e.get("primary"))
            sources = [(e["id"], e) for e in entries[:MAX_CALENDARS]]
        else:
            sources = [self._resolve_calendar(calendar)]
        found, untrusted, skipped = [], False, 0
        for cal_id, entry in sources:
            try:
                result = self.service.events().list(
                    calendarId=cal_id, timeMin=start.isoformat(),
                    timeMax=end.isoformat(), singleEvents=True,
                    orderBy="startTime", maxResults=50, **extra).execute()
            except Exception as e:
                if not merged:
                    raise
                # one unreadable calendar doesn't hide the others
                log.warning("Calendar: skipped a calendar (%s)", type(e).__name__)
                skipped += 1
                continue
            public = is_google_public(cal_id, entry)
            for e in result.get("items", []):
                info = self._summarize(e, cal_id, notes=True)
                if public:  # Google's holidays/birthdays aren't invites
                    info.pop("from_others", None)
                if self._outside(e, cal_id, entry):
                    untrusted = True
                if merged:
                    info["calendar"] = self._calendar_name(entry)
                s, _ = self._event_times(e)
                key = (s if isinstance(s, datetime)
                       else datetime.combine(s, time.min, self.tz))
                found.append((key.astimezone(timezone.utc), info))
        if merged:  # one calendar comes in Google's own order
            found.sort(key=lambda pair: pair[0])
        events = [info for _, info in found]
        out = {"range": spoken_date(first) + (f" plus {days - 1} more days"
                                              if days > 1 else ""),
               "count": len(events), "events": events}
        if merged:
            out["calendars"] = len(sources) - skipped
            if skipped:
                out["unreadable_calendars"] = skipped
        log.info("Calendar: listed %d events from %d calendars", len(events),
                 len(sources))
        if untrusted:
            # Invites, Gmail events, events guests may edit (notes and all)
            # and shared calendars hold untrusted text, so changes this
            # conversation need a yes. Kyle's own events, their notes and
            # Google's holidays/birthdays don't.
            out[UNTRUSTED_MARK] = True
        return out

    def create_event(self, title: str, start: str, end: str | None = None,
                     duration_minutes: int = 60, location: str | None = None,
                     description: str | None = None, color: str | None = None,
                     repeat=None, reminders=None, attendees=None,
                     add_meet: bool = False, show_as: str | None = None,
                     visibility: str | None = None,
                     calendar: str | None = None) -> dict:
        cal_id, _ = self._resolve_calendar(calendar)
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
        if UNTRUSTED_CONTEXT.get():  # its title/notes may be outside text
            _mark_written_under_taint(body)
        options = {}
        if repeat is not None:
            lines = check_repeat(repeat)
            if lines:
                if "dateTime" in body["start"]:
                    self._needs_zone()
                body["recurrence"] = lines
        if reminders is not None:
            body["reminders"] = check_reminders(reminders)
        guests = check_emails(attendees)
        if guests:
            body["attendees"] = [{"email": g} for g in guests]
        if show_as is not None:
            body["transparency"] = SHOW_AS[_choice(show_as, SHOW_AS, "show_as")]
        if visibility is not None:
            body["visibility"] = _choice(visibility, VISIBILITY, "visibility")
        if color is not None and str(color).strip():
            body.update(self.resolve_color(color, cal_id))
            if "eventLabelId" in body:
                options["eventLabelVersion"] = 1
        if add_meet:
            body["conferenceData"] = _meet_request()
            options["conferenceDataVersion"] = 1
        # Guests are emailed only after Kyle's spoken yes (confirm_prompt).
        options["sendUpdates"] = "all" if guests else "none"
        event = self.service.events().insert(calendarId=cal_id, body=body,
                                             **options).execute()
        log.info("Calendar: created an event (%d guests, meet=%s)", len(guests),
                 bool(add_meet))
        out = {"status": "created", "event": self._summarize(event, cal_id)}
        if guests:
            out["invited"] = len(guests)
        return out

    def move_event(self, event_id: str, new_start: str,
                   new_end: str | None = None, scope: str = "this",
                   calendar: str | None = None) -> dict:
        cal_id, entry = self._resolve_calendar(calendar)
        event, target = self._target(cal_id, event_id, scope)
        old_start, old_end = self._event_times(event)
        series = target is not event
        if series:  # the whole series: shift its first one
            first_start, first_end = self._event_times(target)
            patch = self._shift_series(target, self._original_start(event),
                                       first_start, first_end, new_start, new_end)
        elif isinstance(old_start, datetime):
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
        event = self.service.events().patch(calendarId=cal_id,
                                            eventId=target["id"],
                                            body=patch,
                                            sendUpdates="none").execute()
        log.info("Calendar: moved an event (series=%s)", series)
        out = {"status": "moved", "event": self._summarize(event, cal_id)}
        if target.get("recurrence"):
            out["series"] = True
        return mark_untrusted(out, self._outside(event, cal_id, entry))

    def _original_start(self, event: dict) -> datetime | date:
        """Where the series put this occurrence (originalStartTime), even
        if it was moved on its own since; else its start."""
        original = event.get("originalStartTime") or {}
        if "dateTime" in original:
            return parse_datetime(original["dateTime"], self.tz)
        if "date" in original:
            return date.fromisoformat(original["date"])
        return self._event_times(event)[0]

    def _shift_series(self, series, instance_start, first_start, first_end,
                      new_start: str, new_end: str | None) -> dict:
        """Move every occurrence by as much as this one moves from where the
        series put it (wall-clock, so 10 AM stays 10 AM across a DST
        change). `instance_start` is that original start."""
        if isinstance(instance_start, datetime):
            self._needs_zone()
            moved = parse_datetime(new_start, self.tz)
            delta = moved.replace(tzinfo=None) - instance_start.replace(tzinfo=None)
            length = ((parse_datetime(new_end, self.tz) - moved) if new_end
                      else first_end - first_start)
            start_dt = (first_start.replace(tzinfo=None) + delta).replace(tzinfo=self.tz)
            changes_day = moved.date() != instance_start.date()
            new_day, shifted = moved.date(), bool(delta)
            patch = {"start": self._when(start_dt),
                     "end": self._when(start_dt + length)}
        else:
            moved_d = date.fromisoformat(new_start[:10])
            delta = moved_d - instance_start
            length = ((date.fromisoformat(new_end[:10]) + timedelta(days=1) - moved_d)
                      if new_end else first_end - first_start)
            start_d = first_start + delta
            changes_day = bool(delta.days)
            new_day, shifted = moved_d, bool(delta)
            patch = {"start": {"date": start_d.isoformat()},
                     "end": {"date": (start_d + length).isoformat()}}
        rules = list(series.get("recurrence") or [])
        if shifted:
            follow = follow_new_day(rules, new_day if changes_day else None)
            if follow != rules:
                patch["recurrence"] = follow
        return patch

    def delete_event(self, event_id: str, scope: str = "this",
                     calendar: str | None = None) -> dict:
        cal_id, _ = self._resolve_calendar(calendar)
        target_id = event_id
        if _choice(scope or "this", SCOPES_ARG, "scope") == "all":
            _, target = self._target(cal_id, event_id, "all")
            target_id = target["id"]
        self.service.events().delete(calendarId=cal_id, eventId=target_id,
                                     sendUpdates="none").execute()
        log.info("Calendar: deleted an event (series=%s)", target_id != event_id)
        out = {"status": "deleted", "event_id": target_id}
        if target_id != event_id:
            out["series"] = True
        return out

    def update_event(self, event_id: str, title: str | None = None,
                     location: str | None = None, description: str | None = None,
                     color: str | None = None, reminders=None, repeat=None,
                     show_as: str | None = None, visibility: str | None = None,
                     add_attendees=None, remove_attendees=None,
                     add_meet: bool = False, guests_can_modify: bool | None = None,
                     scope: str = "this", calendar: str | None = None) -> dict:
        cal_id, entry = self._resolve_calendar(calendar)
        event, target = self._target(cal_id, event_id, scope)
        series = target is not event or bool(target.get("recurrence"))
        patch, options = {}, {}
        if title is not None:
            patch["summary"] = title
        if location is not None:
            patch["location"] = location
        if description is not None:
            patch["description"] = description
        if UNTRUSTED_CONTEXT.get() and patch:  # title, place or notes
            _mark_written_under_taint(patch, target)
        if reminders is not None:
            patch["reminders"] = check_reminders(reminders)
        if repeat is not None:
            if event.get("recurringEventId") and target is event:
                raise ValueError("The repeat rule belongs to the whole series: "
                                 "use scope \"all\"")
            lines = check_repeat(repeat)
            if not lines:
                raise ValueError("Give the new repeat rule (an RRULE line)")
            patch["recurrence"] = lines
            if "dateTime" in target["start"] and not target["start"].get("timeZone"):
                self._needs_zone()
                start, end = self._event_times(target)
                patch.update(start=self._when(start), end=self._when(end))
        if show_as is not None:
            patch["transparency"] = SHOW_AS[_choice(show_as, SHOW_AS, "show_as")]
        if visibility is not None:
            patch["visibility"] = _choice(visibility, VISIBILITY, "visibility")
        if guests_can_modify is not None:
            patch["guestsCanModify"] = bool(guests_can_modify)
        added = check_emails(add_attendees)
        removed = {e.lower() for e in check_emails(remove_attendees, limit=100)}
        if added or removed:
            guests = [dict(a) for a in target.get("attendees") or []
                      if str(a.get("email", "")).lower() not in removed]
            have = {str(a.get("email", "")).lower() for a in guests}
            guests += [{"email": e} for e in added if e.lower() not in have]
            patch["attendees"] = guests  # patch replaces the whole list
        if add_meet and not target.get("hangoutLink"):
            patch["conferenceData"] = _meet_request()
            options["conferenceDataVersion"] = 1
        clear_label = False
        if color is not None and str(color).strip():
            chosen = self.resolve_color(color, cal_id)
            patch.update(chosen)
            if "eventLabelId" in chosen:
                options["eventLabelVersion"] = 1
            elif target.get("eventLabelId"):
                clear_label = True  # a label hides colorId: take it off first
        if not patch:
            raise ValueError("Nothing to change")
        options["sendUpdates"] = "all" if "attendees" in patch else "none"
        events = self.service.events()
        if clear_label:
            # with eventLabelVersion=1, an empty eventLabelId removes the
            # label (and colorId is ignored), so the color goes in after
            events.patch(calendarId=cal_id, eventId=target["id"],
                         body={"eventLabelId": ""}, eventLabelVersion=1,
                         sendUpdates="none").execute()
        try:
            updated = events.patch(calendarId=cal_id, eventId=target["id"],
                                   body=patch, **options).execute()
        except Exception as e:
            if not clear_label:
                raise
            # the first patch already took the label off: say so
            raise RuntimeError("The label was removed but the color wasn't set "
                               f"({type(e).__name__}); try the color again") from e
        log.info("Calendar: updated an event (%s; series=%s)",
                 ", ".join(sorted(patch)), series)
        out = {"status": "updated", "event": self._summarize(updated, cal_id)}
        if series:
            out["series"] = True
        if added:
            out["invited"] = len(added)
        return mark_untrusted(out, self._outside(updated, cal_id, entry))

    def respond_to_invite(self, event_id: str, response: str,
                          comment: str | None = None, scope: str = "this",
                          calendar: str | None = None) -> dict:
        answer = RESPONSES.get(str(response).strip().lower())
        if answer is None:
            raise ValueError("response must be accepted, declined or tentative")
        cal_id, _ = self._resolve_calendar(calendar)
        _, target = self._target(cal_id, event_id, scope)
        # patch replaces the whole attendees list: send everyone back,
        # changing only this account's own entry
        attendees = [dict(a) for a in target.get("attendees") or []]
        me = next((a for a in attendees if a.get("self") is True), None)
        if me is None:
            raise ValueError("You're not on that event's guest list, so there's "
                             "no invite to answer")
        me["responseStatus"] = answer
        if comment is not None and str(comment).strip():
            me["comment"] = str(comment).strip()[:MAX_COMMENT]
        updated = self.service.events().patch(
            calendarId=cal_id, eventId=target["id"], body={"attendees": attendees},
            sendUpdates="all").execute()
        log.info("Calendar: answered an invite (%s)", answer)
        # the event is someone else's: its title and notes are their text
        return mark_untrusted({"status": "answered", "response": answer,
                               "event": self._summarize(updated, cal_id)}, True)

    def find_free_time(self, day: str = "today", days: int = 1,
                       duration_minutes: int = 30, from_hour: int = 9,
                       to_hour: int = 17) -> dict:
        days = max(1, min(int(days), 14))
        duration = int(duration_minutes)
        if not 1 <= duration <= 24 * 60:
            raise ValueError("duration_minutes must be 1 to 1440")
        if not 0 <= int(from_hour) < int(to_hour) <= 24:
            raise ValueError("from_hour must be before to_hour (0 to 24)")
        first = resolve_day(day, self.tz)
        start = datetime.combine(first, time.min, self.tz)
        end = datetime.combine(first + timedelta(days=days), time.min, self.tz)
        items = self.service.events().list(
            calendarId=self.calendar_id, timeMin=start.isoformat(),
            timeMax=end.isoformat(), singleEvents=True, orderBy="startTime",
            maxResults=250).execute().get("items", [])

        def utc(value):  # instants: wall-clock math breaks across DST
            if not isinstance(value, datetime):
                value = datetime.combine(value, time.min, self.tz)
            return value.astimezone(timezone.utc)

        busy = []
        for e in items:
            if e.get("transparency") == "transparent" or e.get("status") == "cancelled":
                continue
            if any(a.get("self") is True and a.get("responseStatus") == "declined"
                   for a in e.get("attendees") or []):
                continue
            s, en = self._event_times(e)
            busy.append((utc(s), utc(en)))
        busy.sort()
        need = timedelta(minutes=duration)
        # never offer time already gone: from the turn's "now", rounded up
        # to the next 5 minutes (TURN_NOW, so a test can pin it)
        now_utc = turn_now(self.tz).astimezone(timezone.utc)
        earliest = now_utc.replace(second=0, microsecond=0)
        earliest += timedelta(minutes=-earliest.minute % 5)
        if earliest < now_utc:
            earliest += timedelta(minutes=5)
        slots = []
        for i in range(days):
            d = first + timedelta(days=i)
            cursor = utc(datetime.combine(d, time(int(from_hour)), self.tz))
            stop = utc(datetime.combine(d, time(int(to_hour)), self.tz)
                       if int(to_hour) < 24 else d + timedelta(days=1))
            if stop <= earliest:
                continue  # a day (or window) that is already over
            cursor = max(cursor, earliest)
            for b_start, b_end in busy:
                if b_end <= cursor or b_start >= stop:
                    continue
                if b_start - cursor >= need:
                    slots.append((cursor, b_start))
                cursor = max(cursor, b_end)
            if stop - cursor >= need:
                slots.append((cursor, stop))
        free = []
        for s, e in slots[:MAX_FREE_SLOTS]:
            local_s, local_e = s.astimezone(self.tz), e.astimezone(self.tz)
            free.append({"date": spoken_date(local_s.date()),
                         "start": spoken_time(local_s), "end": spoken_time(local_e),
                         "minutes": int((e - s).total_seconds() // 60),
                         "start_iso": local_s.replace(tzinfo=None).isoformat()})
        log.info("Calendar: found %d free slots in %d days", len(slots), days)
        out = {"range": spoken_date(first) + (f" plus {days - 1} more days"
                                              if days > 1 else ""),
               "duration_minutes": duration, "count": len(slots), "free": free}
        if len(slots) > MAX_FREE_SLOTS:
            out["more"] = len(slots) - MAX_FREE_SLOTS
        return out

    def list_calendars(self) -> dict:
        entries = self._calendar_list()
        calendars = [{"name": self._list_name(e), "id": e["id"],
                      "access": e.get("accessRole", "unknown"),
                      **({"primary": True} if e.get("primary") else {})}
                     for e in entries[:50]]
        log.info("Calendar: listed %d calendars", len(calendars))
        # Only short names, never a calendar's description, so a shared
        # calendar on the list doesn't make later changes ask (Kyle's call).
        return {"count": len(entries), "calendars": calendars,
                "colors": self.color_choices()}

    def _list_name(self, entry: dict) -> str:
        """A calendar's name; another person's is cut short (their text)."""
        name = self._calendar_name(entry)
        if entry.get("summaryOverride") or entry.get("accessRole") == "owner":
            return name  # Kyle's own words
        return (name[:MAX_CALENDAR_NAME] + "..."
                if len(name) > MAX_CALENDAR_NAME else name)

    # ----- confirmation questions -----
    def describe_for_delete(self, tool_input: dict) -> str:
        series = str(tool_input.get("scope") or "this").strip().lower() == "all"
        try:
            cal_id, _ = self._resolve_calendar(tool_input.get("calendar"))
            event = self._get(cal_id, tool_input["event_id"])
            info = self._summarize(event, cal_id)
            if series and (event.get("recurringEventId") or event.get("recurrence")):
                return i18n.t("cal_delete_series", title=info["title"])
            if i18n.is_zh():
                start, _ = self._event_times(event)
                when = (i18n.spoken_datetime(start) if isinstance(start, datetime)
                        else f"{start.month}月{start.day}日")
            else:
                when = info["date"] + (f" at {info['start']}" if "start" in info else "")
            return i18n.t("cal_delete", title=info["title"], when=when)
        except Exception:
            return i18n.t("cal_delete_series_any" if series else "cal_delete_any")

    def _spoken_when(self, iso: str) -> str:
        dt = parse_datetime(iso, self.tz)
        if i18n.is_zh():
            return i18n.spoken_datetime(dt)
        return f"{spoken_date(dt.date())} at {spoken_time(dt)}"

    def describe_for_create(self, tool_input: dict) -> str:
        try:
            when = self._spoken_when(tool_input["start"])
            return i18n.t("cal_create", title=tool_input["title"], when=when)
        except Exception:
            return i18n.t("cal_create_any")

    def _invite_question(self, title: str | None, emails) -> str:
        try:
            guests = check_emails(emails)
        except Exception:  # the tool refuses them too: nobody is emailed
            return i18n.t("cal_invite_any")
        return i18n.t("cal_invite", title=title or i18n.t("cal_that_event"),
                      emails=_spoken_list(guests))

    def ask_for_create(self, tool_input: dict) -> str | None:
        """Inviting people emails them: always a spoken yes, naming them.
        Anything else asks only with untrusted text in the conversation."""
        if _as_list(tool_input.get("attendees")):
            return self._invite_question(tool_input.get("title"),
                                         tool_input["attendees"])
        if UNTRUSTED_CONTEXT.get():
            return self.describe_for_create(tool_input)
        return None

    def describe_for_move(self, tool_input: dict) -> str:
        try:
            cal_id, _ = self._resolve_calendar(tool_input.get("calendar"))
            event = self._get(cal_id, tool_input["event_id"])
            title = event.get("summary", "that event")
            when = self._spoken_when(tool_input["new_start"])
            return i18n.t("cal_move", title=title, when=when)
        except Exception:
            return i18n.t("cal_move_any")

    def _event_title(self, tool_input: dict) -> str | None:
        try:
            cal_id, _ = self._resolve_calendar(tool_input.get("calendar"))
            return self._get(cal_id, tool_input["event_id"]).get("summary",
                                                                 "that event")
        except Exception:
            return None

    def describe_for_update(self, tool_input: dict) -> str:
        title = self._event_title(tool_input)
        return (i18n.t("cal_update", title=title) if title is not None
                else i18n.t("cal_update_any"))

    def ask_for_update(self, tool_input: dict) -> str | None:
        """Adding or removing guests emails them: always ask, naming them."""
        added = _as_list(tool_input.get("add_attendees"))
        removed = _as_list(tool_input.get("remove_attendees"))
        if not (added or removed):
            return self.describe_for_update(tool_input) \
                if UNTRUSTED_CONTEXT.get() else None
        title = self._event_title(tool_input)
        questions = []
        if added:
            questions.append(self._invite_question(title, added))
        if removed:
            try:
                gone = check_emails(removed, limit=100)
            except Exception:  # the tool refuses them too
                questions.append(i18n.t("cal_uninvite_any"))
            else:
                questions.append(i18n.t(
                    "cal_uninvite", title=title or i18n.t("cal_that_event"),
                    emails=_spoken_list(gone)))
        return " ".join(questions)

    def describe_for_rsvp(self, tool_input: dict) -> str:
        """Names the answer, the event and its organizer, and reads out any
        note: Google emails it to the organizer (like a draft's text)."""
        answer = RESPONSES.get(str(tool_input.get("response", "")).strip().lower())
        note = str(tool_input.get("comment") or "").strip()[:MAX_COMMENT]
        try:
            if answer is None:
                raise ValueError
            cal_id, _ = self._resolve_calendar(tool_input.get("calendar"))
            event = self._get(cal_id, tool_input["event_id"])
            organizer = event.get("organizer") or {}
            who = organizer.get("displayName") or organizer.get("email") or "someone"
            values = dict(response=i18n.t("rsvp_" + answer),
                          title=event.get("summary", "that event"), organizer=who)
            if note:
                return i18n.t("cal_rsvp_note", comment=note, **values)
            return i18n.t("cal_rsvp", **values)
        except Exception:
            if note:
                return i18n.t("cal_rsvp_note_any", comment=note)
            return i18n.t("cal_rsvp_any")

    def tools(self) -> list[Tool]:
        iso = ("ISO 8601 local date-time without offset, e.g. "
               "2026-10-17T15:00:00")
        aliases = ("; own names: " + ", ".join(
            f"{k} ({v})" for k, v in self.color_labels.items())
            if self.color_labels else "")
        color = {"type": "string", "description":
                 "Google's colors: " + ", ".join(CLASSIC_COLORS.values())
                 + " (or plain words like red or light blue), or the name of "
                 "one of the calendar's event labels" + aliases}
        calendar = {"type": "string", "description":
                    "Optional: another calendar's name or id (default: the "
                    "main calendar)"}
        scope = {"type": "string", "enum": list(SCOPES_ARG), "description":
                 "For a repeating event: 'this' occurrence (default) or "
                 "'all' of them"}
        reminders = {"type": "array", "maxItems": MAX_REMINDERS,
                     "items": {"type": "integer", "minimum": 0,
                               "maximum": MAX_REMINDER_MINUTES},
                     "description": "Popup reminders, minutes before (0 = at "
                                    "the start); [] = no reminders; omit for "
                                    "the calendar's default"}
        repeat = {"type": "array", "maxItems": MAX_REPEAT_LINES,
                  "items": {"type": "string", "maxLength": MAX_REPEAT_LENGTH},
                  "description": "RFC 5545 lines, e.g. "
                                 "[\"RRULE:FREQ=WEEKLY;BYDAY=TU,TH;COUNT=10\"]; "
                                 "EXDATE/RDATE lines also allowed"}
        emails = {"type": "array", "maxItems": MAX_ATTENDEES,
                  "items": {"type": "string"}, "description": "Email addresses"}
        show_as = {"type": "string", "enum": list(SHOW_AS),
                   "description": "Show as busy (default) or free"}
        visibility = {"type": "string", "enum": list(VISIBILITY)}
        tools = [
            Tool("list_calendar_events",
                 "List events on Kyle's Google Calendar for a day (or several "
                 "days starting then), optionally only those matching search "
                 "words. Returns event ids needed to change events, plus each "
                 "event's color or label, repeat, guests and Meet link.",
                 {"type": "object", "properties": {
                     "day": {"type": "string", "description":
                             "'today', 'tomorrow', YYYY-MM-DD (preferred for "
                             "other days), 'friday' (the coming one, or today "
                             "if it is Friday), or 'next friday' (never today)"},
                     "days": {"type": "integer", "minimum": 1, "maximum": 14,
                              "description": "Number of days to include (default 1)"},
                     "query": {"type": "string", "maxLength": MAX_QUERY,
                               "description": "Optional search words (title, "
                                              "place, notes, guests)"},
                     "calendar": {"type": "string", "description":
                                  "Optional: a calendar's name or id, or 'all' "
                                  "for every calendar on the account"}},
                  "required": ["day"]},
                 self.list_events),
            Tool("create_calendar_event",
                 "Create an event on Kyle's calendar. For an all-day event "
                 "pass dates only (YYYY-MM-DD) as start and end. Inviting "
                 "guests emails them, so Kyle is asked first automatically. "
                 + JUST_DO_IT,
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
                     "description": {"type": "string"},
                     "color": color, "repeat": repeat, "reminders": reminders,
                     "attendees": emails,
                     "add_meet": {"type": "boolean",
                                  "description": "Add a Google Meet link"},
                     "show_as": show_as, "visibility": visibility,
                     "calendar": calendar},
                  "required": ["title", "start"]},
                 self.create_event, confirm_prompt=self.ask_for_create,
                 confirm_if_untrusted=self.describe_for_create),
            Tool("move_calendar_event",
                 "Move an existing event to a new time, keeping its length "
                 "unless new_end is given. Get event_id from "
                 "list_calendar_events first. " + JUST_DO_IT,
                 {"type": "object", "properties": {
                     "event_id": {"type": "string"},
                     "new_start": {"type": "string", "description": iso},
                     "new_end": {"type": "string", "description":
                                 iso + ". Optional. For all-day events, the "
                                 "last day (YYYY-MM-DD, inclusive)."},
                     "scope": scope, "calendar": calendar},
                  "required": ["event_id", "new_start"]},
                 self.move_event, confirm_if_untrusted=self.describe_for_move),
            Tool("update_calendar_event",
                 "Change an event's title, place, notes, color or label, "
                 "reminders, repeat rule, busy/free, visibility, guests or "
                 "Meet link (only the fields given change). Get event_id "
                 "from list_calendar_events first. Adding or removing guests "
                 "emails them, so Kyle is asked first automatically. "
                 + JUST_DO_IT,
                 {"type": "object", "properties": {
                     "event_id": {"type": "string"},
                     "title": {"type": "string"}, "location": {"type": "string"},
                     "description": {"type": "string"},
                     "color": color, "reminders": reminders,
                     "repeat": {**repeat, "description": repeat["description"]
                                + " (whole series only: scope 'all')"},
                     "show_as": show_as, "visibility": visibility,
                     "add_attendees": emails,
                     "remove_attendees": {**emails, "maxItems": 100},
                     "add_meet": {"type": "boolean",
                                  "description": "Add a Google Meet link"},
                     "guests_can_modify": {"type": "boolean"},
                     "scope": scope, "calendar": calendar},
                  "required": ["event_id"]},
                 self.update_event, confirm_prompt=self.ask_for_update,
                 confirm_if_untrusted=self.describe_for_update),
            Tool("delete_calendar_event",
                 "Delete an event, or with scope 'all' every occurrence of a "
                 "repeating one. Kyle is asked to confirm out loud "
                 "automatically; just call this tool, don't ask first yourself. "
                 "Get event_id from list_calendar_events first.",
                 {"type": "object", "properties": {
                     "event_id": {"type": "string"}, "scope": scope,
                     "calendar": calendar},
                  "required": ["event_id"]},
                 self.delete_event, confirm_prompt=self.describe_for_delete),
            Tool("respond_to_calendar_invite",
                 "Answer an invite on Kyle's calendar: accepted, declined or "
                 "tentative, with an optional note to the organizer. Kyle is "
                 "asked to confirm out loud automatically.",
                 {"type": "object", "properties": {
                     "event_id": {"type": "string"},
                     "response": {"type": "string",
                                  "enum": ["accepted", "declined", "tentative"]},
                     "comment": {"type": "string", "maxLength": MAX_COMMENT},
                     "scope": scope, "calendar": calendar},
                  "required": ["event_id", "response"]},
                 self.respond_to_invite, confirm_prompt=self.describe_for_rsvp,
                 confirm_if_untrusted=self.describe_for_rsvp),
            Tool("find_free_time",
                 "Find free gaps on Kyle's calendar of at least "
                 "duration_minutes, between from_hour and to_hour each day "
                 "(events shown as free and declined invites don't count).",
                 {"type": "object", "properties": {
                     "day": {"type": "string", "description":
                             "'today', 'tomorrow', YYYY-MM-DD or a weekday"},
                     "days": {"type": "integer", "minimum": 1, "maximum": 14},
                     "duration_minutes": {"type": "integer", "minimum": 1,
                                          "maximum": 1440,
                                          "description": "Default 30"},
                     "from_hour": {"type": "integer", "minimum": 0, "maximum": 23,
                                   "description": "Default 9"},
                     "to_hour": {"type": "integer", "minimum": 1, "maximum": 24,
                                 "description": "Default 17"}},
                  "required": ["day"]},
                 self.find_free_time),
        ]
        if self.extras:  # needs the read-only calendar list scope
            tools.append(Tool(
                "list_calendars",
                "List Kyle's calendars (names, which is main, access) and the "
                "color choices for events: Google's colors, Kyle's event "
                "labels and any color names from the settings.",
                {"type": "object", "properties": {}},
                self.list_calendars))
        return tools


if __name__ == "__main__":  # python -m kwil.tools.calendar  -> sign in once
    from kwil.tools.google_auth import main

    main("python -m kwil.tools.calendar")
