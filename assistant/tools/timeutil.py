"""Time helpers for tools: local zone, parsing, and spoken formatting."""

from __future__ import annotations

from datetime import date, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo


def local_zone(name: str | None = None) -> tzinfo:
    if name:
        return ZoneInfo(name)
    try:
        import tzlocal

        return tzlocal.get_localzone()
    except Exception:
        return datetime.now().astimezone().tzinfo


def zone_name(tz: tzinfo) -> str | None:
    return getattr(tz, "key", None) or getattr(tz, "zone", None)


WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday",
            "saturday", "sunday"]


def resolve_day(day: str, tz: tzinfo) -> date:
    """'today', 'tomorrow', 'yesterday', YYYY-MM-DD, or a weekday name.

    'friday' / 'this friday' is the coming Friday, or today if it is Friday.
    'next friday' is always after today: on a Friday it means a week out.
    """
    today = datetime.now(tz).date()
    d = " ".join((day or "today").strip().lower().split())
    if d == "today":
        return today
    if d == "tomorrow":
        return today + timedelta(days=1)
    if d == "yesterday":
        return today - timedelta(days=1)
    prefix, _, name = d.rpartition(" ")
    if name in WEEKDAYS and prefix in ("", "this", "on", "next"):
        ahead = (WEEKDAYS.index(name) - today.weekday()) % 7
        if prefix == "next" and ahead == 0:
            ahead = 7
        return today + timedelta(days=ahead)
    return date.fromisoformat(d)


def parse_datetime(value: str, tz: tzinfo) -> datetime:
    """ISO 8601 date-time; naive values are taken as local time."""
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)


def spoken_time(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0").replace(":00 ", " ")


def spoken_date(d: date) -> str:
    return f"{d:%A, %B} {d.day}"
