"""Family accounts on the cloud server: Kyle invites someone, they get their
own conversation history and language, and none of Kyle's things.

`[family] enabled` turns it on (the server profile does). The owner (who
signs in with WEB_TOKEN) taps "Invite someone" in the phone app: that makes
a one-time link `https://<origin>/#invite=hcinv.<token>`, valid for
`invite_days`. Opening it signs that phone in as the new person
(POST /api/invite/accept; webapp.py). Only the SHA-256 of the token is kept,
in `<dir>/users.json`; the token itself is shown once and never logged.

What a family member gets (UserContext): web search and fetch (Anthropic's
server tools), their own history at `<dir>/<id>/history.sqlite` (never
synced to the Pi or PC), replies, server sentences and speech in their
language (webapp.WebAssistant picks the Chinese Whisper model and voice),
and, once they tap "Connect Google account" on their own phone, their own
Google Calendar and Gmail (read, drafts only; deletes ask first) from
`<dir>/<id>/google_token.json` (google_web.py signs them in). Their token
is only ever read from that file, never from GOOGLE_TOKEN_JSON (Kyle's),
and Kyle's tools never read theirs. Once they tap "Connect Notion"
(notion_web.py, either tier), their own Notion's Assignments database:
`list_assignments` and `update_assignment_status` only, on their own token
from `<dir>/<id>/notion.enc` (encrypted, userkeys.NOTION_LABEL), never
NOTION_TOKEN (Kyle's). Their own school deadlines ([student] on;
deadlines.py): the deadline tools on `<dir>/<id>/deadlines.sqlite`, filled
from their own Canvas link (`<dir>/<id>/canvas.enc`, encrypted,
canvas.CANVAS_LABEL; student_web.py), and with [canvas] content the
course file tools on `<dir>/<id>/coursefiles.sqlite` (coursefiles.py).
Their own memory ([memory] enabled; memory_store.py): their instructions
and saved memories in `<dir>/<id>/memory.sqlite`, with the remember /
list / update / forget tools and Settings, Memory (memory_web.py).
Their own reminders and scheduled tasks ([reminders] enabled;
reminders.py) in `<dir>/<id>/reminders.sqlite` (reminders_of), with the
create / list / update / cancel tools and Settings, Reminders; they arrive
on their own devices (push.py) in their own time zone.
Their own deep research ([research] enabled; research.py): start_research
on their own key, the jobs in `<dir>/<id>/research/`, the reports in their
own History, Save to Notion into their own Notion.
Nothing else: no Kyle's Notion, Pinecone memory or notes, Kyle's Google,
dev team, lecture recorder or tutor, and no way to see anyone else's
history. Removing someone signs them out and deletes their folder, Google
token, Notion token, deadlines, course files and Canvas link included (the
Google and Notion grants are revoked first, best effort).

Each person has a tier (`tier` in users.json, chosen at invite): "family"
(the above; people listed before tiers existed load as this) or "beta" (an
invite-only public beta tester): Calendar only, never Gmail tools or Gmail
scopes, signed in through a separate OAuth client ([family]
public_google_client / GOOGLE_PUBLIC_CLIENT_JSON) whose consent screen asks
for nothing else. Without that client a beta tester can't connect Google;
they never fall back to the family client (its consent screen lists Gmail).

Whose Claude bill (bring your own key, userkeys.py): someone who saved
their own Anthropic key in the app (PUT /api/me/anthropic-key,
userkeys_web.py) talks to Claude on it, for their chats and their history
titles alike, and that usage is counted in `<dir>/<id>/usage.json`, never
the owner's month_usd. Without one, a "family" person uses the owner's key
(as before); a "beta" tester does only on the starter credit (credit.py:
their first [family] beta_credit_usd, each call reserved and settled by
CreditMessages; never for research, briefings or the email sync), then
their turn stops with CreditSpent ("your $5 starter credit is used up"),
or with the credit off MissingKey ("add your key in Settings"), before any
request is made. The client is
looked up on every call (UserApi), so a new or removed key applies at once,
also to a conversation already open.

Everyone invited, either tier, has limits on turns per minute and per day
and on speech transcribed per day (`Family.quotas`, quotas.py); the owner
has none.
"""

# No `from __future__ import annotations`: FastAPI must see the real
# Request class in the route signatures (imported inside register()).
import hashlib
import hmac
import json
import logging
import re
import secrets
import shutil
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

TOKEN_PREFIX = "hcinv."
LANGUAGES = ("en", "zh")
MAX_NAME = 40
STATUSES = ("invited", "active")
TIERS = ("family", "beta")
DEFAULT_TIER = "beta"      # a new invite; before tiers everyone was family
GOOGLE_TOKEN = "google_token.json"  # in <dir>/<id>/
NOTION_FILE = "notion.enc"          # in <dir>/<id>/: their Notion sign-in, encrypted
_ID = re.compile(r"^u_[A-Za-z0-9_-]{8,40}$")


class FamilyError(ValueError):
    """A request the owner can fix (bad name, too many people...)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class NotionReconnect(RuntimeError):
    """Their Notion sign-in is gone or refused even after a refresh: the
    tool tells Claude (in their language) to have them reconnect it."""


class NotionFailed(RuntimeError):
    """Notion refused a call; the message is Notion's error code only (its
    own message can quote a title)."""


def hash_token(token: str) -> str:
    return hashlib.sha256(token.strip().encode()).hexdigest()


def valid_id(uid: str) -> bool:
    return bool(_ID.match(str(uid or "")))


def clean_name(name) -> str:
    """A display name: printable, one line, at most MAX_NAME characters."""
    if not isinstance(name, str):
        raise FamilyError("give a name")
    name = re.sub(r"\s+", " ", "".join(c for c in name if c.isprintable())).strip()
    if not name:
        raise FamilyError("give a name")
    if len(name) > MAX_NAME:
        raise FamilyError(f"the name can be at most {MAX_NAME} characters")
    return name


def clean_language(language) -> str:
    lang = str(language or "en").lower()
    lang = "zh" if lang.startswith("zh") else lang
    if lang not in LANGUAGES:
        raise FamilyError("language must be en or zh")
    return lang


def clean_timezone(tz) -> str:
    """An IANA zone name ("" = the server's)."""
    tz = str(tz or "").strip()
    if not tz:
        return ""
    if len(tz) > 64:
        raise FamilyError("unknown time zone")
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(tz)
    except Exception:
        raise FamilyError("unknown time zone") from None
    return tz


def clean_tier(tier) -> str:
    tier = str(tier or DEFAULT_TIER).strip().lower()
    if tier not in TIERS:
        raise FamilyError("tier must be family or beta")
    return tier


def tier_of(user: dict | None) -> str:
    """Their tier: "family" if users.json predates tiers; anything else
    unexpected counts as the narrower "beta"."""
    tier = (user or {}).get("tier", "family")
    return tier if tier in TIERS else "beta"


def public(user: dict, credit_used: float | None = None) -> dict:
    """A user as the owner's page sees it (never the invite hash), with
    `credit_used` ($ of the starter credit, credit.py) when given."""
    out = {k: user.get(k) for k in ("id", "name", "language", "timezone",
                                    "created", "status", "invite_expires")}
    out["tier"] = tier_of(user)
    if credit_used is not None:
        out["credit_used_usd"] = credit_used
    return out


@dataclass
class UserContext:
    """One family member's own tools, history and chat maker."""

    user: dict
    registry: object                       # ToolRegistry (shared tool defs)
    history: object | None                 # HistoryStore, or None (history off)
    chat_factory: Callable = field(repr=False)
    memory: object | None = None           # their memory_store.SqliteMemoryStore

    @property
    def language(self) -> str:
        return self.user.get("language", "en")

    @property
    def tier(self) -> str:
        return tier_of(self.user)

    def close(self) -> None:
        for name, store in (("history", self.history), ("memory", self.memory)):
            if store is not None:
                try:
                    store.close()
                except Exception:
                    log.exception("Family: closing a %s store failed", name)


class Family:
    """The invited people, kept in `<dir>/users.json` (written privately and
    atomically). Thread-safe: one lock covers the file, the contexts and
    redeeming, so an invite can't be accepted while it is being removed."""

    def __init__(self, config: dict, client=None, clock=time.time,
                 user_client: Callable[[str], object] | None = None, environ=None):
        from kwil.quotas import UserQuotas
        from kwil.userkeys import KeyStore

        f = config["family"]
        self.config = config
        self.dir = Path(f["dir"])
        self.path = self.dir / "users.json"
        self.max_users = int(f["max_users"])
        self.invite_days = float(f["invite_days"])
        self.max_history_mb = float(f["max_history_mb"])
        self.clock = clock
        self._client = client
        # anthropic.Anthropic for a person's own key (a seam for tests)
        self._user_client = user_client or self._new_user_client
        self.keys = KeyStore(self.dir, environ)  # their Anthropic keys, encrypted
        from kwil.credit import CreditLedger

        # the beta's starter credit on the owner's key (<dir>/beta_credit.json)
        self.credit = CreditLedger(config, self.dir)
        # uid -> their client, or None (no usable key of their own); filled
        # on first use, dropped when they save or remove a key
        self._user_clients: dict[str, object] = {}
        self._meters: dict[str, object] = {}  # uid -> their usage.UsageMeter
        # uid -> (access token, their NotionAssignments on it); and one lock
        # per person so two turns never both refresh their Notion token
        self._notion_apis: dict[str, tuple[str, object]] = {}
        self._notion_locks: dict[str, threading.Lock] = {}
        self.lock = threading.RLock()
        self.users: dict[str, dict] = {}
        # removed people whose folder isn't deleted yet (e.g. a file was
        # locked on Windows): kept in users.json and retried at startup, on
        # the next removal and when the owner opens the list
        self._deleting: set[str] = set()
        self._contexts: dict[str, UserContext] = {}
        # uid -> their reminders.SqliteReminderStore (reminders_of), opened
        # on first use by a tool, a route or the reminder scheduler
        self._reminders: dict[str, object] = {}
        # uid -> does someone have a phone with notifications on? (set by
        # webapp.build_assistant from push.Push.has_devices; None = unknown)
        self.devices: Callable[[str], bool] | None = None
        # research.Research (set by webapp.build_assistant): their own
        # start_research tool and jobs in <dir>/<id>/research; None = none
        self.research = None
        # their turns and speech per minute and day (in memory), never the
        # owner's; a day in their own time zone (users.json is swapped
        # whole, so reading it without the lock is safe)
        self.quotas = UserQuotas.from_config(
            config, clock=clock,
            timezone_of=lambda uid: (self.users.get(uid) or {}).get("timezone"),
            # removed (or being deleted): nothing counted, so a turn in
            # flight can't bring back their counters after forget()
            is_listed=lambda uid: uid in self.users)
        # their school deadlines (deadlines.py), in <dir>/<id>/deadlines.sqlite:
        # the process's one store, which writes for a member only while
        # they are still listed
        self.deadlines = None
        # ... and their Canvas course content ([canvas] content; coursefiles.py)
        # in <dir>/<id>/coursefiles.sqlite, guarded the same way
        self.coursefiles = None
        if config.get("student", {}).get("enabled"):
            from kwil.deadlines import open_store

            self.deadlines = open_store(config)
            self.deadlines.member_guard = self.while_listed
            if config.get("canvas", {}).get("content"):
                from kwil import coursefiles

                self.coursefiles = coursefiles.open_store(config)
                self.coursefiles.member_guard = self.while_listed
        self._load()
        self._finish_deletes()

    @contextmanager
    def while_listed(self, uid: str):
        """Holds the lock; yields whether `uid` is listed (and not being
        deleted), so a write into their folder can't race their removal."""
        with self.lock:
            yield uid in self.users and uid not in self._deleting and valid_id(uid)

    # ---------- the file ----------
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            users = data.get("users", {}) if isinstance(data, dict) else {}
            # silently migrated: no tier = listed before tiers = family
            self.users = {uid: {**u, "tier": tier_of(u)} for uid, u in users.items()
                          if valid_id(uid) and isinstance(u, dict)
                          and u.get("status") in STATUSES}
            deleting = data.get("deleting", []) if isinstance(data, dict) else []
            self._deleting = {uid for uid in deleting if isinstance(uid, str)
                              and valid_id(uid) and uid not in self.users}
        except Exception as e:  # unreadable: nobody can sign in until fixed
            log.error("Family: ignoring unreadable %s (%s)", self.path, type(e).__name__)
            self.users = {}

    def _save(self, users: dict, deleting: set | None = None) -> None:
        """Write `users` (a new copy; the caller swaps it in only after this
        returns, so a failed write changes nothing and can be retried)."""
        from kwil.tools.google_auth import write_private

        deleting = self._deleting if deleting is None else deleting
        self.dir.mkdir(parents=True, exist_ok=True)
        write_private(self.path, json.dumps(
            {"users": users, "deleting": sorted(deleting)}, indent=1))

    # ---------- the owner's actions ----------
    def _new_link(self, user: dict) -> str:
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        user["invite_hash"] = hash_token(token)
        user["invite_expires"] = int(self.clock() + self.invite_days * 86400)
        return token

    def invite(self, name, language="en", timezone="",
               tier=DEFAULT_TIER) -> tuple[dict, str]:
        """A new person and their one-time token (shown once, never stored)."""
        user = {"name": clean_name(name), "language": clean_language(language),
                "timezone": clean_timezone(timezone), "tier": clean_tier(tier)}
        with self.lock:
            if len(self.users) >= self.max_users:
                raise FamilyError(f"at most {self.max_users} people", 409)
            uid = "u_" + secrets.token_urlsafe(9)
            user.update(id=uid, created=int(self.clock()), status="invited")
            token = self._new_link(user)
            users = {**self.users, uid: user}
            self._save(users)
            self.users = users
        log.info("Family: invited %s (%s, %d people)", uid, user["tier"],
                 len(self.users))
        return public(user), token

    def reinvite(self, uid: str) -> tuple[dict, str]:
        """A fresh link for someone (a new phone, or the old link expired);
        any earlier link stops working."""
        with self.lock:
            if uid not in self.users:
                raise FamilyError("not found", 404)
            user = dict(self.users[uid])
            token = self._new_link(user)
            users = {**self.users, uid: user}
            self._save(users)
            self.users = users
        log.info("Family: new invite link for %s", uid)
        return public(user), token

    def members(self) -> list[dict]:
        self._finish_deletes()
        with self.lock:
            return [public(u, self.credit.used(u["id"]))
                    for u in sorted(self.users.values(), key=lambda u: u.get("created", 0))]

    def get(self, uid: str | None) -> dict | None:
        with self.lock:
            user = self.users.get(uid) if uid else None
            return dict(user) if user else None

    def redeem(self, token: str, create: Callable[[str], object]):
        """Accept an invite: `create(uid)` (e.g. the new web session) runs
        with the lock held, so a DELETE can't slip in between. Returns its
        result, or None for an unknown, expired or used token."""
        if not isinstance(token, str) or not token.startswith(TOKEN_PREFIX) \
                or len(token) > 200:
            return None
        digest = hash_token(token)
        now = self.clock()
        with self.lock:
            found = None
            for user in self.users.values():  # compare every one, in constant time
                if hmac.compare_digest(user.get("invite_hash") or "", digest):
                    found = user
            if found is None or not found.get("invite_hash") \
                    or found.get("invite_expires", 0) < now:
                return None
            found = {**found, "status": "active", "invite_hash": None,
                     "invite_expires": None,
                     # when they first signed in (feedback.py's survey)
                     "joined": found.get("joined") or int(now)}
            users = {**self.users, found["id"]: found}
            self._save(users)
            self.users = users
            result = create(found["id"])
        log.info("Family: %s accepted an invite", found["id"])
        return result

    def remove(self, uid: str, before_delete: Callable[[str], None] | None = None) -> bool:
        """Remove someone (invited or active): their entry, then
        `before_delete(uid)` (sign them out), then their history and folder."""
        with self.lock:
            if uid not in self.users:
                return False
            users = {k: v for k, v in self.users.items() if k != uid}
            deleting = self._deleting | {uid} if valid_id(uid) else self._deleting
            self._save(users, deleting)  # OSError: nothing changed, try again
            self.users, self._deleting = users, deleting
            ctx = self._contexts.pop(uid, None)
            reminders = self._reminders.pop(uid, None)
            self._user_clients.pop(uid, None)
            self._meters.pop(uid, None)
            self.credit.forget(uid)  # their row; everyone's total stays
            self._drop_notion_api(uid)
            self._notion_locks.pop(uid, None)
            self.quotas.forget(uid)
            if before_delete is not None:
                before_delete(uid)
        if ctx is not None:
            ctx.close()
        if reminders is not None:  # closed before their folder goes (Windows)
            try:
                reminders.close()
            except Exception:
                log.exception("Family: closing a reminders store failed")
        self._finish_deletes()
        log.info("Family: removed %s (%d people left)", uid, len(self.users))
        return True

    def _finish_deletes(self) -> None:
        """Delete the folders of people already removed from the list; one
        that can't be deleted now stays listed in users.json for next time."""
        with self.lock:
            pending = sorted(self._deleting)
        if not pending:
            return
        done = set()
        for uid in pending:
            try:
                _rmtree(self.dir / uid)
                done.add(uid)
            except OSError:
                log.error("Family: %s's folder couldn't be deleted yet; "
                          "will retry", uid)
        if not done:
            return
        with self.lock:
            self._deleting = self._deleting - done
            try:
                self._save(self.users)
            except OSError as e:  # listed again next start: rmtree is idempotent
                log.error("Family: couldn't save %s (%s)", self.path, type(e).__name__)

    # ---------- what a family member's conversations use ----------
    def context(self, uid: str) -> UserContext | None:
        """Their tools, history and chat factory (made once, then cached);
        None for someone who isn't (or is no longer) listed."""
        with self.lock:
            user = self.users.get(uid)
            if user is None or user.get("status") != "active":
                return None
            ctx = self._contexts.get(uid)
            if ctx is None:
                ctx = self._contexts[uid] = self._build(dict(user))
            return ctx

    def _register_tools(self, user: dict, registry, memory=None) -> None:
        """Web search and fetch, their own deadlines ([student]), their own
        memory (`memory`, their store), and their own Google and Notion
        once connected. No browser (it would open on the server), no Kyle's
        Notion, memory, notes, history, Kyle's Google or dev team."""
        registry.register(*self._google_tools(user), *self._notion_tools(user),
                          *self._deadline_tools(user))
        if memory is not None:
            from kwil.tools.memory_tools import memory_tools

            registry.register(*memory_tools(memory))
        if self.config.get("study", {}).get("enabled", True):
            from kwil.tools.study import study_tools

            # study mode; their missed concepts in their own memory file
            registry.register(*study_tools(memory))
        if memory is not None and self.config.get("projects", {}).get("enabled", True):
            from kwil.tools.projects import project_tools

            # their own projects' files, in their own memory file
            registry.register(*project_tools(memory))
        reminders = self.reminders_of(user["id"])
        if reminders is not None:
            from kwil.tools.reminders import reminder_tools

            uid = user["id"]
            registry.register(*reminder_tools(
                reminders, user.get("timezone") or self.config["assistant"]["timezone"]
                or None, devices=lambda: self.devices is None or self.devices(uid)))
        if self.research is not None:
            from kwil.tools.research import research_tools

            # deep research on their own key and History (research.py)
            registry.register(*research_tools(self.research, user["id"]))
        web = self.config["web"]
        if not web["enabled"]:
            return
        from kwil.tools import web as web_tools

        # their time zone, never Kyle's city
        cfg = {**web, "location": {"timezone": user.get("timezone") or ""}}
        registry.add_server_tools(
            *web_tools.server_tools(self.config["assistant"]["model"], cfg))

    def reminders_of(self, uid: str):
        """Their own reminder store (`<dir>/<id>/reminders.sqlite`), opened
        once; None with [reminders] off, for someone not listed and active
        (or being removed), or if the file can't be opened."""
        if not self.config.get("reminders", {}).get("enabled", True):
            return None
        with self.lock:
            user = self.users.get(uid)
            if user is None or user.get("status") != "active" or not valid_id(uid) \
                    or uid in self._deleting:
                return None
            store = self._reminders.get(uid)
            if store is None:
                from kwil.reminders import SqliteReminderStore

                try:  # their own file, never the owner's
                    store = self._reminders[uid] = SqliteReminderStore(
                        self.dir / uid / "reminders.sqlite")
                except Exception as e:
                    log.error("Family: %s's reminders couldn't be opened (%s)", uid,
                              type(e).__name__)
                    return None
            return store

    # ---------- their own Google (google_web.py signs them in) ----------
    def google_token(self, uid: str) -> Path:
        return self.dir / uid / GOOGLE_TOKEN

    def tier(self, uid: str | None) -> str:
        """The owner (None) counts as family; a uid that isn't listed (e.g.
        removed while signing in) gets the narrower beta, never family."""
        if not uid:
            return "family"
        with self.lock:
            user = self.users.get(uid)
        return tier_of(user) if user is not None else "beta"

    def google_client(self, uid: str | None = None) -> dict | None:
        """The Web application OAuth client `uid` signs in with: family
        members the family client, beta testers only the public one (never
        the family client, whose consent screen lists Gmail). None (their
        Google is off) if it isn't set up."""
        from kwil.tools.google_auth import PUBLIC_CLIENT_ENV, web_client_config

        f = self.config["family"]
        beta = uid is not None and self.tier(uid) == "beta"
        which = "public" if beta else "web"
        try:
            client = (web_client_config(f["public_google_client"], env=PUBLIC_CLIENT_ENV)
                      if beta else web_client_config(f["google_client"]))
        except (OSError, ValueError) as e:  # never the value
            log.warning("Family: the Google %s client is unreadable (%s)", which, e)
            return None
        if client is not None and "web" not in client:
            log.warning("Family: the Google %s client is not a Web application client",
                        which)
            return None
        return client

    def google_enabled(self, uid: str | None = None) -> bool:
        return bool(self.config["family"]["google"] and own_origin(self.config)
                    and self.google_client(uid))

    def gmail_allowed(self, uid: str | None) -> bool:
        """May `uid` have Gmail at all? Never a beta tester."""
        return bool(self.config["gmail"]["enabled"]) and self.tier(uid) == "family"

    def google_connected(self, uid: str) -> bool:
        return valid_id(uid) and self._google_info(uid) is not None

    def _google_info(self, uid: str) -> dict | None:
        from kwil.tools.google_auth import load_token_info

        try:  # environ={}: their file only, never GOOGLE_TOKEN_JSON
            return load_token_info(self.google_token(uid), environ={})
        except (OSError, ValueError):
            return None

    def _google_tools(self, user: dict) -> list:
        """Calendar and Gmail on their own token (none until they connect).
        Never Kyle's: `environ={}` hides GOOGLE_TOKEN_JSON/GOOGLE_CLIENT_JSON,
        `save_token=False` never starts a sign-in on the server, `in_app`
        makes an expired or revoked token say "reconnect in the app", and
        the calendar is their primary one."""
        if not (self.config["family"]["google"] and valid_id(user.get("id"))):
            return []
        info = self._google_info(user["id"])
        if info is None:
            return []
        from kwil.tools.calendar import GoogleCalendar
        from kwil.tools.google_auth import CALENDAR_EXTRA_SCOPES, granted_scopes

        token, tz = self.google_token(user["id"]), user.get("timezone") or None
        # their labels and calendars only if their token allows it; never
        # Kyle's [calendar] color_labels
        tools = GoogleCalendar(credentials_path="", token_path=token,
                               calendar_id="primary", timezone=tz,
                               save_token=False, environ={}, in_app=True,
                               account=user["id"],
                               extras=set(CALENDAR_EXTRA_SCOPES) <= granted_scopes(info)
                               ).tools()
        inbox = self.gmail_inbox(user, info)
        if inbox is not None:
            tools += inbox.tools()
        # the descriptions say "Kyle's calendar": make them about this person
        name = "the user" if user.get("language") == "zh" else user["name"]
        return [replace(t, description=t.description.replace("Kyle's", f"{name}'s")
                        .replace("Kyle", name)) for t in tools]

    def gmail_inbox(self, user: dict, info: dict | None = None):
        """Their own Gmail (read, drafts only; never Kyle's), or None: a
        beta tester, Gmail off, not connected, or no Gmail scopes granted.
        Also read by student_web's sync (Canvas emails, read-only)."""
        from kwil.tools.google_auth import GMAIL_SCOPES, granted_scopes

        if not (self.config["family"]["google"] and self.config["gmail"]["enabled"]
                and tier_of(user) == "family" and valid_id(user.get("id"))):
            return None
        info = self._google_info(user["id"]) if info is None else info
        if info is None or not set(GMAIL_SCOPES) <= granted_scopes(info):
            return None
        from kwil.tools.gmail import GmailInbox

        return GmailInbox(credentials_path="", token_path=self.google_token(user["id"]),
                          timezone=user.get("timezone") or None,
                          max_summary=self.config["gmail"]["max_summary"],
                          save_token=False, environ={}, in_app=True,
                          account=user["id"])

    def _deadline_tools(self, user: dict) -> list:
        """Their own deadlines (deadlines.py), in their own time zone, and
        their own course content (coursefiles.py)."""
        if self.deadlines is None or not valid_id(user.get("id")):
            return []
        from kwil.tools.deadlines import DeadlineTools

        tools = DeadlineTools(self.deadlines, user["id"], user.get("timezone") or None).tools()
        if self.coursefiles is not None:
            from kwil.tools.coursefiles import CourseFileTools

            tools += CourseFileTools(self.coursefiles, user["id"]).tools()
        return tools

    def save_google(self, uid: str, token_text: str) -> bool:
        """Store their token (privately, atomically) and add their Google
        tools to their open conversations. False if they were removed
        meanwhile (nothing written: their folder is going away)."""
        from kwil.tools.google_auth import clear_reconnect, write_private

        with self.lock:
            user = self.users.get(uid)
            if user is None or user.get("status") != "active" or not valid_id(uid):
                return False
            path = self.google_token(uid)
            path.parent.mkdir(parents=True, exist_ok=True)
            write_private(path, token_text)
            clear_reconnect(uid)  # the new sign-in replaces the refused one
            self._refresh_tools(uid)
        log.info("Family: %s connected their Google account", uid)
        return True

    def disconnect_google(self, uid: str) -> bool:
        """Revoke their grant at Google, then delete their token and take
        their Google tools away. False if there was nothing to delete.
        The revoke is best effort (account.py's helper, as "Delete my
        account" and Remove): a failure is logged by type and never blocks
        the delete. Either tier: the revoke needs only the token."""
        from kwil.account import revoke_google_grant
        from kwil.tools.google_auth import clear_reconnect

        with self.lock:
            if uid not in self.users or not valid_id(uid):
                return False
        # outside the lock: the network call (up to its timeout) mustn't
        # hold up everyone else's sign-ins and tools
        revoke_google_grant(self, uid, label="Family")
        with self.lock:
            if uid not in self.users:  # removed meanwhile: the folder went
                return False
            path = self.google_token(uid)
            had = path.exists()
            _unlink(path)
            clear_reconnect(uid)
            self._refresh_tools(uid)
        log.info("Family: %s disconnected their Google account", uid)
        return had

    # ---------- their own Notion (notion_web.py signs them in) ----------
    def notion_file(self, uid: str) -> Path:
        return self.dir / uid / NOTION_FILE

    def notion_enabled(self, uid: str | None = None) -> bool:
        """May an invited person (either tier) connect Notion here? Needs
        [family] notion, the public integration's id and secret, and this
        server's address (the redirect URI)."""
        from kwil.notion_web import oauth_client

        return bool(self.config["family"]["notion"] and own_origin(self.config)
                    and oauth_client() is not None)

    def _notion_record(self, uid: str) -> dict | None:
        """Their saved sign-in (access_token, refresh_token, workspace_*,
        database_id, data_source_id, database_title), or None."""
        from kwil.userkeys import NOTION_LABEL, unseal

        if not valid_id(uid):
            return None
        try:
            raw = self.notion_file(uid).read_bytes()
        except FileNotFoundError:
            return None
        except OSError as e:
            log.warning("Family: %s's Notion file is unreadable (%s)", uid, type(e).__name__)
            return None
        data = unseal(NOTION_LABEL, uid, raw, self.keys.environ, what="Notion sign-in",
                      redo="connect Notion again")
        if data is None or not isinstance(data.get("access_token"), str) \
                or not data["access_token"]:
            return None
        return data

    def notion_status(self, uid: str) -> dict:
        """{connected, workspace, database} for their Settings; never a token."""
        record = self._notion_record(uid) if uid in self.users else None
        if record is None:
            return {"connected": False, "workspace": None, "database": None}
        return {"connected": True, "workspace": record.get("workspace_name"),
                "database": (record.get("database_title") or None)
                if record.get("data_source_id") else None}

    def notion_connected(self, uid: str) -> bool:
        return self.notion_status(uid)["connected"]

    def save_notion(self, uid: str, record: dict, tools: bool = True,
                    expect: str | None = None) -> bool:
        """Store their sign-in (encrypted, privately, atomically) and give
        their open conversations the tools. False if they were removed
        meanwhile, or (`expect`, a refresh) the stored access token is no
        longer `expect` (disconnected or reconnected meanwhile): nothing
        written. KeyProblem 503 without USER_DATA_KEY."""
        from kwil.tools.google_auth import write_private
        from kwil.userkeys import NOTION_LABEL, seal

        record = {k: v for k, v in record.items() if k != "uid"}
        with self.lock:
            user = self.users.get(uid)
            if user is None or user.get("status") != "active" or not valid_id(uid):
                return False
            if expect is not None:
                stored = self._notion_record(uid)
                if stored is None or stored["access_token"] != expect:
                    log.info("Family: %s's Notion sign-in changed meanwhile; "
                             "not saving", uid)
                    return False
            text = seal(NOTION_LABEL, uid, record, self.keys.environ)
            path = self.notion_file(uid)
            path.parent.mkdir(parents=True, exist_ok=True)
            write_private(path, text)
            self._notion_apis.pop(uid, None)  # the next call uses the new token
            if tools:
                self._refresh_tools(uid)
        log.info("Family: %s saved their Notion sign-in", uid)
        return True

    def _notion_lock(self, uid: str) -> threading.Lock:
        """Their Notion lock: a refresh, a (re)connect's save, and a
        disconnect or revoke run one at a time, so none of them brings back
        a sign-in another one just replaced or deleted. Kept for listed
        people only (bounded); anyone else gets a lock of their own."""
        with self.lock:
            if uid not in self.users:
                return threading.Lock()
            return self._notion_locks.setdefault(uid, threading.Lock())

    def replace_notion(self, uid: str, record: dict) -> tuple[bool, str | None]:
        """save_notion() for a (re)connect: (saved, the access token it
        replaced if that was another grant, for the caller to revoke). Not
        when it's the same bot (Notion may hand the same bot a new token
        on a reconnect to the same workspace; revoking the old one could
        end the new grant too)."""
        from kwil.notion_web import same_bot

        with self._notion_lock(uid):
            before = self._notion_record(uid)
            if not self.save_notion(uid, record):
                return False, None
        if before is None or before["access_token"] == record["access_token"] \
                or same_bot(before, record):
            return True, None
        return True, before["access_token"]

    def _revoke_notion(self, uid: str) -> None:
        """Best effort: ask Notion to revoke their stored grant (disconnect,
        "Delete my account", the owner removing them). The caller holds
        their Notion lock (not reentrant), so a refresh in flight finished
        first and the token revoked is the one stored. Never raises: a
        failure never blocks deleting."""
        try:
            record = self._notion_record(uid)
            if record is None:
                return
            from kwil.notion_web import revoke_quietly

            revoke_quietly(record["access_token"], uid)
        except Exception as e:  # noqa: BLE001 - deleting goes ahead regardless
            log.warning("Family: revoking %s's Notion failed (%s)", uid, type(e).__name__)

    def disconnect_notion(self, uid: str) -> bool:
        """Revoke (best effort), delete their sign-in, take the tools away.
        Under their Notion lock: a refresh in flight finishes first (its
        new token is the one revoked), and none can save in between.
        False if there was nothing to delete."""
        if uid not in self.users or not valid_id(uid):
            return False
        with self._notion_lock(uid):
            self._revoke_notion(uid)  # the network: outside self.lock
            with self.lock:
                path = self.notion_file(uid)
                had = path.exists()
                _unlink(path)
                self._drop_notion_api(uid)
                self._refresh_tools(uid)
        log.info("Family: %s disconnected their Notion", uid)
        return had

    def _drop_notion_api(self, uid: str) -> None:
        with self.lock:
            cached = self._notion_apis.pop(uid, None)
        if cached is not None:
            try:
                cached[1]._client.close()  # never .client: it can read NOTION_TOKEN
            except Exception:  # noqa: BLE001 - only frees a connection pool
                log.debug("Family: closing a Notion client failed", exc_info=True)

    def _notion_api(self, uid: str, record: dict):
        """Their NotionAssignments on `record`'s token (cached per token).
        Always given its own client: with a falsy token, NotionAssignments
        would fall back to NOTION_TOKEN (Kyle's)."""
        from kwil import notion_web
        from kwil.tools.notion import NotionAssignments

        token = record["access_token"]
        with self.lock:  # one check-and-swap: no network here
            cached = self._notion_apis.get(uid)
            if cached is not None and cached[0] == token:
                return cached[1]
            user = self.users.get(uid) or {}
            made = NotionAssignments(record["database_id"], client=notion_web.api(token),
                                     data_source_id=record["data_source_id"],
                                     timezone=user.get("timezone") or None)
            if uid in self.users:  # bounded: listed people only
                # an older one is left to the GC, not closed: another turn
                # may still be using it
                self._notion_apis[uid] = (token, made)
        return made

    def refresh_notion(self, uid: str, failed: str) -> dict | None:
        """After a 401 on access token `failed`: their record with a new
        access token, or None (reconnect). One refresh at a time per person;
        a turn that waited uses the token the other one got. Keeps the old
        refresh token if Notion sends none back."""
        from kwil import notion_web
        from kwil.userkeys import KeyProblem

        if uid not in self.users:
            return None
        with self._notion_lock(uid):
            record = self._notion_record(uid)
            if record is None:
                return None
            if record["access_token"] != failed:
                return record  # refreshed meanwhile
            if not record.get("refresh_token"):
                return None
            try:
                answer = notion_web.refresh_grant(record["refresh_token"])
                access = notion_web.record_of(answer)["access_token"]
            except Exception as e:  # never the message
                log.warning("Family: refreshing %s's Notion token failed (%s)",
                            uid, type(e).__name__)
                return None
            fresh = {**record, "access_token": access,
                     "refresh_token": (answer.get("refresh_token")
                                       if isinstance(answer.get("refresh_token"), str)
                                       and answer["refresh_token"]
                                       else record["refresh_token"])}
            try:
                saved = self.save_notion(uid, fresh, tools=False, expect=failed)
            except (KeyProblem, OSError) as e:
                log.warning("Family: saving %s's refreshed Notion token failed (%s)",
                            uid, type(e).__name__)
                saved = False
            if not saved:  # not stored: never leave the new token live at Notion
                notion_web.revoke_quietly(access, uid)
                return None
        log.info("Family: refreshed %s's Notion token", uid)
        return fresh

    def _notion_tools(self, user: dict) -> list:
        """list_assignments and update_assignment_status on their own
        Notion (none until they connect and one Assignments database was
        found). Both results hold Notion text: untrusted (a title could be
        written to steer Claude) and private (titles stay out of the log)."""
        if not (self.config["family"]["notion"] and valid_id(user.get("id"))):
            return []
        uid = user["id"]
        record = self._notion_record(uid)
        if record is None or not record.get("database_id") \
                or not record.get("data_source_id"):
            return []
        lang = user.get("language")
        methods = {"list_assignments": "list_assignments",
                   "update_assignment_status": "update_status"}
        name = "the user" if lang == "zh" else user["name"]
        out = []
        for t in self._notion_api(uid, record).tools():
            if t.name not in methods:
                continue
            extra = {}
            if t.confirm_if_untrusted is not None:
                extra["confirm_if_untrusted"] = self._notion_question(uid)
            out.append(replace(
                t, handler=self._notion_handler(uid, methods[t.name], lang),
                untrusted_output=True, private=True,
                description=t.description.replace("Kyle's", f"{name}'s")
                .replace("Kyle", name).replace(" his ", " their "), **extra))
        return out

    def _notion_question(self, uid: str):
        def ask(tool_input: dict) -> str:
            record = self._notion_record(uid)
            try:
                return self._notion_api(uid, record).describe_for_update(tool_input)
            except Exception:  # noqa: BLE001 - disconnected meanwhile: still ask
                what = tool_input.get("name") or "that assignment"
                return f"Do you want me to mark {what} as {tool_input.get('status')}?"
        return ask

    def _notion_handler(self, uid: str, method: str, lang: str | None):
        """A tool handler on their current token: a 401 refreshes it once
        (refresh_notion), then says "reconnect Notion". Notion's own error
        messages (they can quote a title) never reach the log."""
        from kwil import i18n

        def call(record, kwargs):
            from notion_client.errors import is_notion_client_error

            try:
                return getattr(self._notion_api(uid, record), method)(**kwargs), None
            except Exception as e:
                if not is_notion_client_error(e):
                    raise
                if getattr(e, "status", None) == 401:
                    return None, e
                code = getattr(getattr(e, "code", None), "value", None) or type(e).__name__
                raise NotionFailed(f"Notion said {code}") from None

        def run(**kwargs):
            record = self._notion_record(uid)
            if record is None or not record.get("data_source_id"):
                raise NotionReconnect(i18n.t("notion_reconnect", lang))
            result, denied = call(record, kwargs)
            if denied is None:
                return result
            record = self.refresh_notion(uid, record["access_token"])
            if record is None:
                raise NotionReconnect(i18n.t("notion_reconnect", lang))
            result, denied = call(record, kwargs)
            if denied is not None:
                raise NotionReconnect(i18n.t("notion_reconnect", lang))
            return result

        return run

    def _refresh_tools(self, uid: str) -> None:
        """Their open conversations' tools after a connect or disconnect:
        every SessionTools shares this registry's dict, so update it in
        place, adding before removing (a running turn never sees it empty)."""
        ctx = self._contexts.get(uid)
        if ctx is None:
            return
        from kwil.tools import ToolRegistry

        fresh = ToolRegistry()
        self._register_tools(ctx.user, fresh, ctx.memory)
        ctx.registry.tools.update(fresh.tools)
        for name in set(ctx.registry.tools) - set(fresh.tools):
            ctx.registry.tools.pop(name, None)

    def _build(self, user: dict) -> UserContext:
        from kwil.chat import ClaudeChat
        from kwil.history import HistoryStore, Recorder, origin_of
        from kwil.tools import ToolRegistry

        a, h = self.config["assistant"], self.config["history"]
        memory = None
        if self.config.get("memory", {}).get("enabled", True):
            from kwil.memory_store import SqliteMemoryStore

            try:  # their own file, never the owner's
                memory = SqliteMemoryStore(self.dir / user["id"] / "memory.sqlite")
            except Exception as e:  # they still get answers, without memory
                log.error("Family: %s's memory couldn't be opened (%s)",
                          user["id"], type(e).__name__)
        registry = ToolRegistry()
        self._register_tools(user, registry, memory)
        store = None
        if h["enabled"]:
            # Their own file, opened directly: history.open_store() would
            # replace the owner's store that record_event() writes to.
            # eventlog=False: nothing to sync to Kyle's Pi or PC.
            # [family] max_history_mb, not the owner's [history] max_mb: many
            # people share one small server disk.
            store = HistoryStore(self.dir / user["id"] / "history.sqlite",
                                 retention_days=h["retention_days"],
                                 max_mb=self.max_history_mb,
                                 origin=origin_of(self.config),
                                 eventlog=False)
            from kwil import recall

            recall.attach(store, self.config)  # their own index, in their file

        from kwil import projects, thinkhard

        bounds = projects.limits(self.config)  # their project files' budget
        project_options = {"project_inline_tokens": bounds["inline_tokens"],
                           "project_inline_bytes": bounds["inline_bytes"]}

        def chat_factory(session_registry):
            # their own key (or, for family, the owner's), looked up per call
            # on the starter credit too (credit.py): their chats, titles and
            # scheduled reminder tasks (which run on this factory)
            chat_api = UserApi(self, user["id"], credit=True)
            title_api = UserApi(self, user["id"], credit=True)
            recorder = Recorder(store, "web", client_fn=title_api.client_or_none,
                                title_model=h["title_model"], meter=title_api)
            return ClaudeChat(client=chat_api, meter=chat_api, model=a["model"],
                              max_tokens=a["max_tokens"],
                              max_tokens_tools=a["max_tokens_tools"],
                              max_history_chars=a["max_history_chars"],
                              thinking_turns=a["thinking_turns"],
                              max_history=a["max_history"], tools=session_registry,
                              max_tool_rounds=a["max_tool_rounds"], memory=None,
                              recorder=recorder, language=user["language"],
                              user_name=user["name"],
                              timezone=user.get("timezone") or None,
                              fast_model=a["fast_model"], router=a["router"],
                              local_memory=memory, **project_options,
                              **thinkhard.options_from_config(self.config),
                              # their own daily count (quotas.py)
                              think_gate=lambda: self.quotas.admit_think(user["id"]))

        return UserContext(user, registry, store, chat_factory, memory)

    def client(self, uid: str | None = None):
        """The owner's client (uid None), else the one `uid`'s calls use:
        their own key's, the owner's for a "family" person without one, or
        MissingKey (a beta tester without a key, or someone no longer listed;
        never the starter credit: a bare client's calls can't be reserved)."""
        if uid is not None:
            return self.api_for(uid)[0]
        if self._client is None:
            import anthropic

            a = self.config["assistant"]
            self._client = anthropic.Anthropic(max_retries=a["api_max_retries"],
                                               timeout=a["api_timeout"])
        return self._client

    def _new_user_client(self, key: str):
        from kwil.userkeys import user_client

        a = self.config["assistant"]  # the same options as the owner's
        # only their key: never ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN
        # from the server's environment (Kyle's)
        return user_client(key, max_retries=a["api_max_retries"],
                           timeout=a["api_timeout"])

    def api_for(self, uid: str, credit: bool = False) -> tuple[object, bool]:
        """(client, True if it is on their own key) for `uid`'s next call."""
        client, own, _ = self.api_of(uid, credit)
        return client, own

    def api_of(self, uid: str, credit: bool = False) -> tuple[object, bool, bool]:
        """api_for, and whether the call is on the starter credit. A beta
        tester without a key gets the owner's client while some of their
        starter credit is left (credit.py; `credit=False`: never, e.g. deep
        research); the call itself reserves its worst case (UserApi's
        CreditMessages). CreditSpent once it is used up, MissingKey with
        the credit off."""
        from kwil.userkeys import MissingKey

        with self.lock:
            user = self.users.get(uid)
            if user is None or not valid_id(uid):  # removed: nobody's key
                raise MissingKey("not a listed person")
            if uid not in self._user_clients:
                key = self.keys.load(uid)
                self._user_clients[uid] = self._user_client(key) if key else None
            own = self._user_clients[uid]
        if own is not None:
            return own, True, False
        if tier_of(user) == "family":
            return self.client(), False, False
        if not (credit and self.credit.enabled):
            raise MissingKey()
        if not self.credit.check(uid):
            raise self.credit_spent(uid)
        return self.client(), False, True

    def credit_spent(self, uid: str):
        """The CreditSpent to raise for `uid` (logged by id)."""
        from kwil.userkeys import CreditSpent

        log.info("Credit: %s's starter credit is used up", uid)
        return CreditSpent(self.credit.per_person, self.credit.help_url(self.config))

    # ---------- their own Anthropic key (userkeys_web.py) ----------
    def key_status(self, uid: str) -> dict:
        """What their Settings shows: {set, last4, unreadable, required,
        available, month_usd}. Never the key."""
        with self.lock:
            if uid not in self.users:
                raise FamilyError("not found", 404)
            key = self.keys.load(uid)
            tier = tier_of(self.users[uid])
        out = {"set": key is not None, "last4": key[-4:] if key else None,
               # a saved key that no longer opens (USER_DATA_KEY changed)
               "unreadable": key is None and self.keys.stored(uid),
               "required": tier == "beta", "available": self.keys.problem() is None}
        if key is not None:  # their own estimate (usage.py prices)
            out["month_usd"] = self.meter(uid).month_usd()
        if tier == "beta" and self.credit.enabled:  # the starter credit (credit.py)
            out["credit_usd"] = self.credit.per_person
            out["credit_left_usd"] = self.credit.left(uid)
        return out

    def set_key(self, uid: str, key: str) -> bool:
        """Save their (already checked) key; their next call uses it. False
        if they were removed meanwhile (nothing written)."""
        with self.lock:
            if self.users.get(uid, {}).get("status") != "active" or not valid_id(uid):
                return False
            self.keys.save(uid, key)  # KeyProblem 503: no USER_DATA_KEY
            self._user_clients.pop(uid, None)
        return True

    def delete_key(self, uid: str) -> bool:
        """Delete their key; their next call is on the owner's key (family)
        or stops with MissingKey (beta). False if there was none."""
        with self.lock:
            if uid not in self.users or not valid_id(uid):
                return False
            had = self.keys.delete(uid)
            self._user_clients.pop(uid, None)
        return had

    def meter(self, uid: str):
        """Their own usage.UsageMeter (`<dir>/<id>/usage.json`)."""
        from kwil.usage import UsageMeter

        with self.lock:
            if uid not in self._meters:
                self._meters[uid] = UsageMeter(self.dir / uid / "usage.json")
            return self._meters[uid]

    def record_usage(self, uid: str, model: str, usage) -> None:
        """Count a call made on `uid`'s own key on their meter, unless they
        were removed meanwhile (their folder is going away)."""
        with self.lock:
            if uid in self.users:
                self.meter(uid).record(model, usage)

    def set_survey(self, uid: str, state: str) -> bool:
        """Mark their one-time survey (feedback.py) "answered" or
        "dismissed" in users.json (a flag, never their answers). False if
        they aren't listed."""
        with self.lock:
            user = self.users.get(uid)
            if user is None or not valid_id(uid):
                return False
            if user.get("survey") == state:
                return True
            users = {**self.users, uid: {**user, "survey": state}}
            self._save(users)
            self.users = users
        return True

    def close(self) -> None:
        with self.lock:
            contexts, self._contexts = list(self._contexts.values()), {}
            reminders, self._reminders = list(self._reminders.values()), {}
        for ctx in contexts:
            ctx.close()
        for store in reminders:
            try:
                store.close()
            except Exception:
                log.exception("Family: closing a reminders store failed")


class UserApi:
    """The Anthropic client one person's conversation uses, looked up on
    every use (Family.api_of), so a key they save or remove applies to the
    very next call. ClaudeChat's `client` and `meter` (and the Recorder's):
    usage on their own key goes on their meter, on the owner's key to the
    owner's (usage.METER). Raises MissingKey on use when they have none.
    A call on the beta's starter credit goes through CreditMessages, which
    reserves and settles it (credit.py). `with_options` keeps the wrapper."""

    def __init__(self, family: "Family", uid: str, credit: bool = False,
                 options: dict | None = None):
        self._family = family
        self._uid = uid
        self._credit = credit  # may a beta tester's call use the starter credit?
        self._options = options or {}
        self.own_key = False  # whose key the last call used (cli.friendly_error)
        self.on_credit = False  # ... and whether that was the starter credit

    def resolve(self):
        client, self.own_key, self.on_credit = self._family.api_of(self._uid, self._credit)
        if self._options and callable(getattr(client, "with_options", None)):
            return client.with_options(**self._options)
        return client

    def client_or_none(self):
        """For history titles: this wrapper (so a title on the starter
        credit is reserved and settled too), or None without a key (the
        Recorder then uses the first words as the title)."""
        from kwil.userkeys import MissingKey

        try:
            self.resolve()
        except MissingKey:
            return None
        return self

    def with_options(self, **options) -> "UserApi":
        view = UserApi(self._family, self._uid, self._credit,
                       {**self._options, **options})
        return view

    @property
    def messages(self):
        client = self.resolve()
        if not self.on_credit:
            return client.messages
        # the SDK's own retries would bill each timed-out try and settle one:
        # CreditMessages retries itself, reserving and settling every try
        retries = getattr(client, "max_retries", 0)
        retries = retries if isinstance(retries, int) and not isinstance(retries, bool) \
            else 0
        if callable(getattr(client, "with_options", None)):
            client = client.with_options(max_retries=0)
        return CreditMessages(self._family, self._uid, client.messages, retries)

    def __getattr__(self, name):  # anything else the SDK client has
        if name.startswith("_"):
            raise AttributeError(name)
        client = self.resolve()
        if self.on_credit:  # only .messages is reserved and settled
            raise AttributeError(f"{name} isn't available on the starter credit")
        return getattr(client, name)

    def record(self, model: str, usage) -> None:
        """The bill's meter (the credit is settled by CreditMessages)."""
        if self.own_key:
            self._family.record_usage(self._uid, model, usage)
        else:
            from kwil import usage as owner_usage

            owner_usage.record(model, usage)


def _reached_anthropic(error: BaseException) -> bool:
    """May a failed call have been billed? Not if Anthropic answered with an
    error status (it bills only what it answers), nor if the connection was
    never made; anything else (a timeout, a dropped stream) may have been."""
    import anthropic

    if isinstance(error, anthropic.APIStatusError):
        return False
    seen, e = set(), error
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        # httpx's or httpx2's (newer SDKs): by name, either library
        if any(c.__name__ in ("ConnectError", "ConnectTimeout") for c in type(e).__mro__):
            return False
        e = e.__cause__ or e.__context__
    return True


def _retry_delay(error: BaseException, attempt: int) -> float | None:
    """Seconds before trying again after `error` (try number `attempt`,
    from 0), or None (not worth another try). The SDK's own rules
    (anthropic._base_client): timeouts, connection errors, 408, 409, 429
    and 5xx, an `x-should-retry` header winning; a `retry-after(-ms)` of at
    most 60 s, else 0.5 s doubling up to 8 s, less up to 25% jitter."""
    import random

    import anthropic

    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None) or {}
    if isinstance(error, anthropic.APIStatusError):
        should = str(headers.get("x-should-retry", "")).lower()
        if should == "false":
            return None
        status = error.status_code
        if should != "true" and status not in (408, 409, 429) and status < 500:
            return None
        for name, scale in (("retry-after-ms", 0.001), ("retry-after", 1.0)):
            try:
                wait = float(headers.get(name)) * scale
            except (TypeError, ValueError):
                continue
            if 0 < wait <= 60:
                return wait
    elif not isinstance(error, anthropic.APIConnectionError):  # timeouts too
        return None
    return min(0.5 * 2 ** attempt, 8.0) * (1 - 0.25 * random.random())


_sleep = time.sleep  # a seam for tests


class CreditMessages:
    """`client.messages` for a call on the starter credit: right before it
    is sent, the request's worst case is reserved (credit.request_reserve;
    CreditSpent if it doesn't fit), and when it ends, however it ends, the
    reservation is settled in a `finally` (credit.py says with what). The
    client under it doesn't retry: each of the `retries` tries this makes
    (the SDK's rules and backoff, _retry_delay) is reserved and settled on
    its own, so a timed-out try that was billed is charged too."""

    def __init__(self, family: "Family", uid: str, messages, retries: int = 0):
        self._family = family
        self._uid = uid
        self._messages = messages
        self._retries = max(0, int(retries))

    def _again(self, error: BaseException, attempt: int) -> bool:
        """After a failed try: wait and say True if another one is due."""
        if attempt >= self._retries:
            return False
        delay = _retry_delay(error, attempt)
        if delay is None:
            return False
        log.info("Credit: %s's call failed (%s); try %d of %d in %.1f s", self._uid,
                 type(error).__name__, attempt + 2, self._retries + 1, delay)
        _sleep(delay)
        return True

    def _admit(self, kwargs: dict):
        from kwil.credit import request_reserve

        reserve = request_reserve(kwargs)
        token = self._family.credit.admit(self._uid, reserve)
        if token is None:
            raise self._family.credit_spent(self._uid)
        return token, reserve

    def _settle(self, token, reserve: float, model: str, usage=None, error=None,
                partial: bool = False, max_tokens: int = 0) -> None:
        from kwil import usage as usage_mod
        from kwil.credit import call_cost

        if usage is not None:
            counts = usage_mod.counts(usage)
            if partial:  # cut short: the output so far isn't counted yet
                counts["output"] = max(counts["output"], max_tokens)
            usd = call_cost(model, counts)
        elif error is not None and not _reached_anthropic(error):
            usd = 0.0
        else:
            usd = reserve  # no usage at all: what it could have cost
        self._family.credit.settle(self._uid, usd, token)

    def create(self, **kwargs):
        attempt = 0
        while True:
            try:
                return self._try_create(kwargs)
            except Exception as e:
                if not self._again(e, attempt):
                    raise
            attempt += 1

    def _try_create(self, kwargs: dict):
        token, reserve = self._admit(kwargs)
        usage, error = None, None
        try:
            response = self._messages.create(**kwargs)
            usage = getattr(response, "usage", None)
            return response
        except BaseException as e:
            error = e
            raise
        finally:
            self._settle(token, reserve, str(kwargs.get("model") or ""), usage, error)

    def stream(self, **kwargs):
        return _CreditStream(self, kwargs)

    def __getattr__(self, name):
        if name == "count_tokens":  # free; anything else would bill unreserved
            return self._messages.count_tokens
        raise AttributeError(f"messages.{name} isn't available on the starter credit")


class _CreditStream:
    """The context manager messages.stream returns, settling on exit."""

    def __init__(self, owner: CreditMessages, kwargs: dict):
        self._owner = owner
        self._kwargs = kwargs
        self._manager = None
        self._stream = None
        self._token = None
        self._reserve = 0.0

    def __enter__(self):
        attempt = 0
        while True:  # the request itself, retried like create (once streaming: never)
            self._token, self._reserve = self._owner._admit(self._kwargs)
            try:
                self._manager = self._owner._messages.stream(**self._kwargs)
                self._stream = self._manager.__enter__()
                return self._stream
            except BaseException as e:
                self._owner._settle(self._token, self._reserve, self._model(), error=e)
                if not isinstance(e, Exception) or not self._owner._again(e, attempt):
                    raise
            attempt += 1

    def _model(self) -> str:
        return str(self._kwargs.get("model") or "")

    def __exit__(self, kind, error, tb):
        try:
            return self._manager.__exit__(kind, error, tb)
        finally:
            snapshot = _snapshot(self._stream, error)
            usage = getattr(snapshot, "usage", None) if snapshot is not None else None
            partial = error is not None or getattr(snapshot, "stop_reason", None) is None
            self._owner._settle(self._token, self._reserve, self._model(), usage,
                                error if usage is None else None, partial=partial,
                                max_tokens=int(self._kwargs.get("max_tokens") or 0))


def _snapshot(stream, error):
    """The stream's message: the final one after a clean end, else what
    came so far (the SDK's current_message_snapshot, which asserts before
    the first event), or None."""
    if stream is None:
        return None
    if error is None:
        try:
            return stream.get_final_message()
        except Exception:  # noqa: BLE001 - cut short after all: the snapshot
            pass
    try:
        return stream.current_message_snapshot
    except (AssertionError, AttributeError):
        return None


def _unlink(path: Path, attempts: int = 20) -> None:
    """Delete a file; retried like _rmtree (a reader may hold it on Windows)."""
    for attempt in range(attempts):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.05)


def _rmtree(folder: Path, attempts: int = 20) -> None:
    """Delete a folder; on Windows a file another thread just closed can be
    locked for a moment, so retry."""
    for attempt in range(attempts):
        try:
            shutil.rmtree(folder)
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.1)


# ---------- HTTP (added by webapp.create_app) ----------

def invite_url(origin: str, token: str) -> str:
    """The link to send: the token rides in the #fragment, which browsers
    never send to a server (family.js reads it, clears it and POSTs it)."""
    return f"{origin.rstrip('/')}/#invite={token}"


def https_url(value) -> str:
    """`value` if it is an https:// link with a host (and no spaces), else
    "" (a setting left empty or mistyped is never shown)."""
    from urllib.parse import urlsplit

    if not isinstance(value, str) or not value or any(c.isspace() for c in value):
        return ""
    try:
        parts = urlsplit(value)
    except ValueError:
        return ""
    return value if parts.scheme == "https" and parts.netloc else ""


def invite_message(family: "Family", user: dict, url: str, origin: str) -> str:
    """The invite text in their language: the link, and for a beta tester
    the key guide, Google's "unverified app" step, the starter credit, and
    the Notion template and chat links when set (https only)."""
    from kwil import i18n

    lang = user.get("language") or "en"
    name = user.get("name") or ""
    if tier_of(user) != "beta":
        return i18n.t("invite_family", lang, name=name, url=url)
    from kwil.credit import HELP_PATH

    f = family.config["family"]
    lines = [i18n.t("invite_beta", lang, name=name, url=url,
                    help=f"{origin.rstrip('/')}{HELP_PATH}")]
    if family.credit.enabled:
        usd = family.credit.per_person
        lines.append(i18n.t("invite_credit", lang,
                            usd=f"{usd:.0f}" if float(usd).is_integer() else f"{usd:.2f}"))
    for key, setting in (("invite_template", "notion_template_url"),
                         ("invite_chat", "beta_chat_url")):
        link = https_url(f.get(setting))
        if link:
            lines.append(i18n.t(key, lang, url=link))
    return "\n".join(lines)


def own_origin(config: dict) -> str:
    """[webapp] public_origin, else (with failover) this backend's own URL
    (the cloud's defaults to APP_ORIGIN); "" = unknown."""
    configured = config["webapp"].get("public_origin") or ""
    if configured:
        return configured
    try:
        from kwil.failover.peers import settings_from_config

        settings = settings_from_config(config)
    except Exception:  # a bad [failover] section is reported elsewhere
        return ""
    own = [b.url for b in settings.backends if settings.role and b.role == settings.role]
    return own[0] if own else ""


def register(app, family: "Family | None", web, assistant, error) -> None:
    """The owner's family routes and the public invite acceptance:

      POST   /api/invites             {name, language, timezone?, tier?} -> {url, expires_at}
                                      (tier "beta", the default, or "family")
      GET    /api/users               the invited people (owner only)
      POST   /api/users/{id}/remove   revoke their Google and Notion grants
                                      (best effort), sign them out, delete
                                      their history
                                      (also DELETE /api/users/{id}; the page
                                      posts: the failover preflight allows
                                      only GET and POST cross-origin)
      POST   /api/users/{id}/invite   a new link (the old one stops working)
      POST   /api/invite/accept       {invite} -> session cookie (public)

    Owner routes answer 404 to a family member (and with [family] off).
    Wrong, expired or used invites count toward the sign-in lockout."""
    from fastapi import Depends, Request
    from fastapi.responses import JSONResponse
    from starlette.concurrency import run_in_threadpool

    from kwil import webapp

    async def body_of(request: Request) -> dict:
        try:
            body = await request.json()
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}

    def origin_of(request: Request) -> str:
        """This server's own address (where /api/invite/accept is
        same-origin), never the page's: with failover the owner's page may
        be the Pi's or the app origin, calling here cross-origin."""
        return own_origin(family.config) or f"https://{request.url.netloc}"

    def link(request: Request, user: dict, token: str):
        url = invite_url(origin_of(request), token)
        return {"user": user, "url": url, "expires_at": user["invite_expires"],
                # the text to send them (PR-B's page shares it)
                "message": invite_message(family, user, url, origin_of(request))}

    def off():
        return error("not found", 404) if family is None else None

    @app.post("/api/invites")
    async def create_invite(request: Request, session=Depends(webapp.require_owner)):
        if (e := off()) is not None:
            return e
        body = await body_of(request)
        try:
            user, token = await run_in_threadpool(
                family.invite, body.get("name"), body.get("language") or "en",
                body.get("timezone") or "", body.get("tier") or DEFAULT_TIER)
        except FamilyError as e:
            return error(str(e), e.status)
        return link(request, user, token)

    @app.get("/api/users")
    def list_users(session=Depends(webapp.require_owner)):
        if (e := off()) is not None:
            return e
        return {"users": family.members(), "max_users": family.max_users,
                **family.credit.totals()}

    @app.post("/api/users/{uid}/invite")
    async def new_link(uid: str, request: Request,
                       session=Depends(webapp.require_owner)):
        if (e := off()) is not None:
            return e
        try:
            user, token = await run_in_threadpool(family.reinvite, uid)
        except FamilyError as e:
            return error(str(e), e.status)
        return link(request, user, token)

    def sign_out(uid: str) -> None:
        """Called with the family lock held, before their files go."""
        sids = web.store.revoke_user(uid)
        live = getattr(app.state, "voice_conversations", {})
        for sid in sids:
            entry = live.get(sid)
            if entry is not None:
                try:
                    entry[0].end("signed_out")
                except Exception:
                    log.exception("Family: hanging up a voice conversation failed")
        assistant.drop_user(uid)

    app.state.family_sign_out = sign_out  # account.py's "Delete my account"

    @app.post("/api/users/{uid}/remove")
    @app.delete("/api/users/{uid}")
    async def remove_user(uid: str, session=Depends(webapp.require_owner)):
        if (e := off()) is not None:
            return e

        def revoke_and_remove(uid: str) -> bool:
            # the revokes are best effort, never block the removal (the
            # token files go with the folder). Google first, as "Delete my
            # account" does (account.py's helper), only for someone listed:
            # uid comes from the path. Notion under their Notion lock, so a
            # refresh in flight finishes first and none saves in between
            if family.get(uid) is not None:
                from kwil.account import revoke_google_grant

                revoke_google_grant(family, uid, label="Family")
            with family._notion_lock(uid):
                family._revoke_notion(uid)
                return family.remove(uid, sign_out)

        try:
            removed = await run_in_threadpool(revoke_and_remove, uid)
        except OSError:
            return error("couldn't remove them; try again", 500)
        if not removed:
            return error("not found", 404)
        return {"ok": True}

    @app.post("/api/invite/accept")
    async def accept(request: Request):
        if (e := off()) is not None:
            return e
        given = (await body_of(request)).get("invite")
        sid = await run_in_threadpool(family.redeem, given, web.store.create)
        if sid is None:  # wrong, expired or already used: like a wrong token
            ip = web.client_ip(request)
            if web.limiter.blocked(ip):
                return error("too many attempts; try again later", 429)
            delay = web.limiter.fail(ip)
            log.warning("Web: a wrong, expired or used invite")
            if delay:
                await webapp._wait(delay)
            return error("this invite link has expired or was already used", 401)
        response = JSONResponse({"ok": True})
        webapp.set_session_cookie(response, web.token, sid, web.store.epoch)
        log.info("Web: family sign-in ok (%s)", web.store.user(sid))
        return response
