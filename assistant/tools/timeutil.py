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


def resolve_day(day: str, tz: tzinfo) -> date:
    """'today', 'tomorrow', a weekday name, or YYYY-MM-DD."""
    today = datetime.now(tz).date()
    d = (day or "today").strip().lower()
    if d == "today":
        return today
    if d == "tomorrow":
        return today + timedelta(days=1)
    if d == "yesterday":
        return today - timedelta(days=1)
    weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday",
                "saturday", "sunday"]
    if d in weekdays:
        ahead = (weekdays.index(d) - today.weekday()) % 7
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
