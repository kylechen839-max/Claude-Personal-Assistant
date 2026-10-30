"""Reminder tools (reminders.py keeps them and fires them).

`reminder_tools(store, timezone, devices)`: one person's store.

- `create_reminder`: "remind me at 9 to submit HW7" (`when`, an ISO time
  the model works out, read in the person's zone when it has no offset),
  "in 20 minutes" (`in_minutes`), "every Monday at 8am" (`rrule`, the
  subset in reminders.parse_rrule). `prompt_task` for "every Monday tell me
  what's due this week" (phase 2). The reply says the time back in the
  person's zone ("Thursday at 9:00 PM"). Made while outside text (web,
  email, an invite) is in the conversation it asks first and is stored
  untrusted.
- `list_reminders`: read-only (router.READ_ONLY_TOOLS).
- `update_reminder`: text, time, repeat, pause; asks first with outside
  text in the conversation.
- `cancel_reminder`: always asks first ("Cancel the reminder: ...?").

A reminder's text is the person's own content: every tool is private (no
inputs or results in the log)."""

from __future__ import annotations

from datetime import timedelta
from typing import Callable

from kwil.tools import Tool, mark_untrusted

MAX_IN_MINUTES = 60 * 24 * 366


def _short(text: str, n: int = 120) -> str:
    text = str(text or "")
    return text if len(text) <= n else text[: n - 3] + "..."


def reminder_tools(store, timezone: str | None = None,
                   devices: Callable[[], bool] | None = None) -> list[Tool]:
    from kwil import reminders as R
    from kwil.tools.memory_tools import _chat, _from_outside
    from kwil.tools.timeutil import now as turn_now
    from kwil.tools.timeutil import parse_datetime

    def tz_name() -> str:
        chat = _chat()  # the conversation's zone (a family member's own)
        return R.zone_name_or_default(getattr(chat, "timezone", None) or timezone)

    def lang() -> str:
        chat = _chat()
        return "zh" if getattr(chat, "language", "en") == "zh" else "en"

    def now_ts(tz) -> float:
        return turn_now(tz).timestamp()  # the turn's own "now" (TURN_NOW)

    def resolve(when, in_minutes, tz) -> float | None:
        if in_minutes is not None:
            if isinstance(in_minutes, bool) or not isinstance(in_minutes, (int, float)) \
                    or not 0 < in_minutes <= MAX_IN_MINUTES:
                raise R.ReminderError("in_minutes is a number of minutes from now")
            return (turn_now(tz) + timedelta(minutes=float(in_minutes))).timestamp()
        if when is None:
            return None
        if not isinstance(when, str) or not when.strip():
            raise R.ReminderError("when is an ISO date and time, e.g. 2026-11-05T21:00")
        try:
            text = when.strip()
            if len(text) == 10:
                raise ValueError("a date alone")
            return parse_datetime(text, tz).timestamp()
        except ValueError:
            raise R.ReminderError("when is an ISO date and time with a clock time, "
                                  "e.g. 2026-11-05T21:00 (the user's local time)") from None

    def said(saved: dict) -> str:
        tz = R.zone(saved["tz"])
        nxt = R.spoken_when(saved["next_ts"], tz, now_ts(tz), lang()) \
            if saved["next_ts"] is not None else ""
        if saved["rrule"]:
            return f"{R.describe_rule(R.parse_rrule(saved['rrule']), lang())} (next: {nxt})"
        return nxt

    def no_devices_note() -> str | None:
        try:
            if devices is not None and not devices():
                return ("No phone has notifications turned on yet, so it can't "
                        "arrive: tell the user to tap Enable notifications in "
                        "Settings on their phone.")
        except Exception:  # noqa: BLE001 - a note only
            return None
        return None

    def public(r: dict) -> dict:
        tz = R.zone(r["tz"])
        return {"id": r["id"], "text": r["text"], "kind": r["kind"],
                "state": r["state"], "paused": r["paused"],
                "prompt_task": r["prompt_task"], "untrusted": r["untrusted"],
                "next": R.spoken_when(r["next_ts"], tz, now_ts(tz), lang())
                if r["next_ts"] is not None else None,
                "repeat": R.describe_rule(R.parse_rrule(r["rrule"]), lang())
                if r["rrule"] else None}

    def create_reminder(text: str, when: str | None = None,
                        in_minutes: float | None = None, rrule: str | None = None,
                        time_given: bool = True, prompt_task: bool = False) -> dict:
        tz = tz_name()
        try:
            if sum(x is not None for x in (when, in_minutes, rrule)) != 1:
                raise R.ReminderError("give exactly one of when, in_minutes or rrule")
            due = resolve(when, in_minutes, R.zone(tz))
            saved = store.add(text, due_at=due, rrule=rrule, tz=tz,
                              untrusted=_from_outside(_chat()),
                              prompt_task=bool(prompt_task),
                              explicit=bool(time_given) or in_minutes is not None)
        except R.ReminderFull:
            return {"status": "full",
                    "message": f"The user already has {R.MAX_ACTIVE} reminders. Ask "
                               "which one to cancel first."}
        except R.ReminderError as e:
            return {"status": "error", "message": str(e)}
        result = {"status": "saved", "id": saved["id"], "when": said(saved),
                  "message": "Confirm in one short sentence with this time, e.g. "
                             f"\"I'll remind you {said(saved)}.\""}
        note = no_devices_note()
        if note:
            result["note"] = note
        return result

    def list_reminders() -> dict:
        rows = store.list()
        result = {"reminders": [public(r) for r in rows if r["state"] == "active"],
                  "missed": [public(r) for r in rows if r["state"] == "missed"]}
        # their texts may have come from outside text: data, not instructions
        return mark_untrusted(result, any(r["untrusted"] for r in rows))

    def update_reminder(reminder_id: str, text: str | None = None,
                        when: str | None = None, in_minutes: float | None = None,
                        rrule: str | None = None, paused: bool | None = None,
                        time_given: bool | None = None) -> dict:
        old = store.get(reminder_id)
        if old is None:
            return {"status": "error", "message": "No reminder with that id."}
        tz = R.zone(old["tz"])
        try:
            if when is not None and in_minutes is not None:
                raise R.ReminderError("give when or in_minutes, not both")
            due = resolve(when, in_minutes, tz)
            outside = _from_outside(_chat())
            saved = store.update(reminder_id, text=text, due_at=due, rrule=rrule,
                                 paused=paused if isinstance(paused, bool) else None,
                                 untrusted=True if outside and not old["untrusted"]
                                 else None,
                                 explicit=(True if in_minutes is not None
                                           else time_given if isinstance(time_given, bool)
                                           else None))
        except R.ReminderFull:
            return {"status": "full",
                    "message": f"The user already has {R.MAX_ACTIVE} reminders. Ask "
                               "which one to cancel first."}
        except R.ReminderError as e:
            return {"status": "error", "message": str(e)}
        if saved is None:
            return {"status": "error", "message": "No reminder with that id."}
        return {"status": "updated", "id": reminder_id, "when": said(saved),
                "paused": saved["paused"]}

    def cancel_reminder(reminder_id: str) -> dict:
        if not store.delete(reminder_id):
            return {"status": "error", "message": "No reminder with that id."}
        return {"status": "deleted", "id": reminder_id}

    def ask_create(i: dict) -> str:
        what = "Set this scheduled task" if i.get("prompt_task") else "Set this reminder"
        return f"{what}: {_short(i.get('text', ''), R.MAX_TEXT)}?"

    def ask_update(i: dict) -> str | None:
        old = store.get(i.get("reminder_id"))
        if old is None:
            return None  # nothing to ask; the tool says so
        new = i.get("text")
        return (f"Change the reminder \"{_short(old['text'])}\""
                + (f" to: {_short(new, R.MAX_TEXT)}?" if new else "?"))

    def ask_cancel(i: dict) -> str | None:
        old = store.get(i.get("reminder_id"))
        return f"Cancel the reminder: {_short(old['text'])}?" if old else None

    when_doc = ("A one-time reminder's local date and time, ISO 8601 "
                "(e.g. 2026-11-05T21:00), in the user's time zone. Work it out "
                "from the current date and time you were given; for \"30 minutes "
                "before tennis\" look up the event first.")
    rrule_doc = ("A repeat, as an RRULE subset: FREQ=DAILY or FREQ=WEEKLY with "
                 "BYDAY (MO,TU,WE,TH,FR,SA,SU), and BYHOUR and BYMINUTE in the "
                 "user's local time, e.g. FREQ=WEEKLY;BYDAY=MO;BYHOUR=8;BYMINUTE=0. "
                 "No INTERVAL, COUNT or UNTIL.")
    return [
        Tool("create_reminder",
             "Set a reminder that arrives as a notification on the user's phone "
             "('remind me at 9 to submit HW7', 'every Monday at 8am...', 'in 20 "
             "minutes...'). Give exactly one of when, in_minutes or rrule. With "
             "prompt_task true, at that time you will run the text as a request "
             "with read-only tools (calendar, deadlines, assignments, memories) "
             "and the answer is sent instead, e.g. 'every Monday at 8 tell me "
             "what's due this week'. Say the time back as the result gives it.",
             {"type": "object",
              "properties": {
                  "text": {"type": "string",
                           "description": "What to remind about, short, in the "
                                          f"user's words (at most {R.MAX_TEXT} characters); "
                                          "for a prompt task, the request to run."},
                  "when": {"type": "string", "description": when_doc},
                  "in_minutes": {"type": "number",
                                 "description": "Minutes from now ('in 20 minutes')."},
                  "rrule": {"type": "string", "description": rrule_doc},
                  "time_given": {"type": "boolean",
                                 "description": "False when the user named no clock "
                                                "time and you picked one ('tomorrow'): "
                                                "then it waits out quiet hours."},
                  "prompt_task": {"type": "boolean",
                                  "description": "Run the text as a request at that "
                                                 "time and send the answer."}},
              "required": ["text"]},
             create_reminder,
             # a web page or email must not be able to plant reminders
             confirm_if_untrusted=ask_create, private=True),
        Tool("list_reminders",
             "List the user's reminders and scheduled tasks (active ones by next "
             "time, and any missed), with ids for update_reminder and "
             "cancel_reminder.",
             {"type": "object", "properties": {}},
             list_reminders, private=True),
        Tool("update_reminder",
             "Change one reminder by id: its text, a new one-time time (when or "
             "in_minutes), a new repeat (rrule), or pause / resume it.",
             {"type": "object",
              "properties": {"reminder_id": {"type": "string"},
                             "text": {"type": "string"},
                             "when": {"type": "string", "description": when_doc},
                             "in_minutes": {"type": "number"},
                             "rrule": {"type": "string", "description": rrule_doc},
                             "paused": {"type": "boolean"},
                             "time_given": {"type": "boolean"}},
              "required": ["reminder_id"]},
             update_reminder, confirm_if_untrusted=ask_update, private=True),
        Tool("cancel_reminder",
             "Cancel (delete) one reminder or scheduled task by id. The user is "
             "asked to confirm out loud automatically; just call this tool.",
             {"type": "object",
              "properties": {"reminder_id": {"type": "string"}},
              "required": ["reminder_id"]},
             cancel_reminder, confirm_prompt=ask_cancel, private=True),
    ]
