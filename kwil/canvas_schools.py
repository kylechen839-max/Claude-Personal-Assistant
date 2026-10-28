"""Whether a school's Canvas lets students make an access token, so the
School deadlines card (web/student.js) can pick the way in by itself: an
access token where tokens are allowed, the Canvas helper or the calendar
feed where they are blocked or nobody knows yet.

Nothing public says (Canvas shows the setting only to its admins), so two
sources are combined, per Canvas host:

- the seed, `kwil/data/canvas_schools.json`: schools whose own page
  someone opened and read, each with its `source` and the date it was
  `checked`. Adding a school is a data change only (check_entry is the
  schema; tests check every entry). A host matches an entry listed in
  `hosts`, or ending in "." + one of its `domains`; a `*.instructure.com`
  host only by being listed (no domain may cover instructure.com).
- what this server learned, `<[student] dir>/canvas_policy.json`:
  {host: {allowed, blocked, allowed_at, blocked_at, last, day_allowed,
  day_blocked}}, counts and dates only (never who; day_*: the counts on
  the `last` day), at most MAX_HOSTS hosts (the least recently seen go
  first), written with google_auth.write_private. "allowed": the first
  working sync of a token just connected in the app (once per link), or
  the Canvas helper reading that a student-only account may make one;
  "blocked": the helper reading that this account may not, or a person
  saying their Canvas has no New access token button. A 401 or 403 says
  nothing about the policy (an expired or revoked token, a permission)
  and is never recorded. The callers count each person's claim once
  (student_web, canvas_helper), so counts are separate events.

policy(host): what was learned, when that is newer than the seed's
`checked` date (policies change: a learned result from before the seed was
checked never overrides it), else the seed, else "unknown". The outcome
seen on the latest day wins; on the same day the one counted more that
day, and a tie is "blocked" (a token that worked may be a TA's or older
than a restriction; the helper and the feed always work). Each report is
someone's claim: validated, counted, never trusted over a newer seed.

What was learned names schools people on this server use, so it is only
revealed (`own=False`) once it rests on MIN_EVENTS events or more;
for the asker's own Canvas host (`own=True`) it always is.

A host names the person's school, so it is never logged (outcomes and
counts only); nothing here leaves the server.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

SEED = Path(__file__).parent / "data" / "canvas_schools.json"
POLICY_FILE = "canvas_policy.json"
MAX_HOSTS = 1000                  # learned hosts kept, at most
MAX_COUNT = 1_000_000             # a counter stops there
MIN_EVENTS = 2                    # learned events before others may see a host's result
TOKENS = ("allowed", "blocked")   # a seed entry's student_tokens, and what is recorded
ENTRY_KEYS = frozenset({"name", "hosts", "domains", "student_tokens", "since", "source",
                        "checked", "note"})
_HOST = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_LOCAL = re.compile(r"\.(local|localhost|internal|lan|home|arpa|test|invalid|example)$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
INSTRUCTURE = "instructure.com"


def normalize(value) -> str | None:
    """A Canvas host ("canvas.school.edu") from a host or an https URL, in
    lower case, or None: a dotted public DNS name only (no IP, port,
    user:password@ or LAN-only name), as web/student.js schoolHost."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > 300:
        return None
    if "://" in text:
        try:
            url = urlsplit(text)
            port = url.port
        except ValueError:
            return None
        if url.scheme.lower() != "https" or url.username or url.password \
                or port not in (None, 443):
            return None
        text = url.hostname or ""
    elif re.search(r"[/:@?#\s]", text):
        return None
    host = text.lower().rstrip(".")
    if not _HOST.match(host) or _LOCAL.search(host):
        return None
    return host


def _day(value) -> date | None:
    if not isinstance(value, str) or not _DATE.match(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def check_entry(entry) -> dict:
    """A seed entry, normalized; ValueError saying what is wrong."""
    if not isinstance(entry, dict):
        raise ValueError("an entry is an object")
    extra = set(entry) - ENTRY_KEYS
    if extra:
        raise ValueError(f"unknown field(s): {', '.join(sorted(extra))}")
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        raise ValueError("name: the school's name")
    if entry.get("student_tokens") not in TOKENS:
        raise ValueError("student_tokens: blocked or allowed")
    source = entry.get("source")
    if not isinstance(source, str) or not source.startswith("https://") or len(source) > 500 \
            or re.search(r"\s", source):
        raise ValueError("source: the https page that says so")
    checked = _day(entry.get("checked"))
    if checked is None:
        raise ValueError("checked: YYYY-MM-DD, when the source was read")
    since = entry.get("since")
    if since is not None and _day(since) is None:
        raise ValueError("since: YYYY-MM-DD or null")
    note = entry.get("note", "")
    if not isinstance(note, str) or len(note) > 500:
        raise ValueError("note: text")
    hosts, domains = [], []
    for field, out in (("hosts", hosts), ("domains", domains)):
        values = entry.get(field, [])
        if not isinstance(values, list) or len(values) > 20:
            raise ValueError(f"{field}: a list")
        for v in values:
            host = normalize(v)
            if host is None or host != v:
                raise ValueError(f"{field}: lower-case host names only")
            out.append(host)
    if not hosts and not domains:
        raise ValueError("hosts or domains: at least one")
    for d in domains:
        if d == INSTRUCTURE or d.endswith("." + INSTRUCTURE):
            raise ValueError("domains: list *.instructure.com hosts in hosts")
    return {"name": name.strip(), "hosts": hosts, "domains": domains,
            "student_tokens": entry["student_tokens"], "since": since,
            "source": source, "checked": checked, "note": note}


def load_seed(path: Path | str = SEED) -> list[dict]:
    """The seed's checked entries; an entry that doesn't pass check_entry
    is skipped (with a warning naming its index, never its content), and
    an unreadable file is an empty seed."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning("Canvas schools: the seed is unreadable (%s)", type(e).__name__)
        return []
    if not isinstance(data, dict) or data.get("version") != 1 \
            or not isinstance(data.get("schools"), list):
        log.warning("Canvas schools: the seed isn't version 1 with a schools list")
        return []
    out = []
    for i, entry in enumerate(data["schools"]):
        try:
            out.append(check_entry(entry))
        except ValueError as e:
            log.warning("Canvas schools: seed entry %d skipped (%s)", i, e)
    return out


def seed_for(host: str, seed: list[dict]) -> dict | None:
    """The seed entry for a normalized host: listed in `hosts`, or ending
    in "." + one of `domains` (the most recently checked, if several)."""
    found = [e for e in seed
             if host in e["hosts"] or any(host.endswith("." + d) for d in e["domains"])]
    return max(found, key=lambda e: e["checked"]) if found else None


def _count(value) -> int:
    return min(value, MAX_COUNT) if isinstance(value, int) and not isinstance(value, bool) \
        and value > 0 else 0


class Policies:
    """The seed plus what this server learned, for one [student] dir."""

    def __init__(self, directory: Path | str, seed: Path | str = SEED, today=None):
        self.path = Path(directory) / POLICY_FILE
        self.seed_path = Path(seed)
        self.today = today or (lambda: datetime.now(timezone.utc).date())
        self.lock = threading.Lock()
        self._seed: list[dict] | None = None
        self._learned: dict | None = None
        self._stamp = None   # the file's (mtime_ns, size) when it was read

    def seed(self) -> list[dict]:
        if self._seed is None:
            self._seed = load_seed(self.seed_path)
        return self._seed

    def _read(self) -> dict:
        """The learned table (cached until the file changes), checked:
        anything that isn't a normalized host with counts and dates is
        dropped."""
        try:
            st = self.path.stat()
            stamp = (st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            self._learned, self._stamp = {}, None
            return {}
        except OSError:
            return dict(self._learned or {})
        if self._learned is not None and stamp == self._stamp:
            return self._learned
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.warning("Canvas schools: the learned file is unreadable (%s)", type(e).__name__)
            raw = {}
        out = {}
        for host, v in (raw.items() if isinstance(raw, dict) else []):
            if normalize(host) != host or not isinstance(v, dict) or _day(v.get("last")) is None:
                continue
            out[host] = {"allowed": _count(v.get("allowed")), "blocked": _count(v.get("blocked")),
                         "allowed_at": v.get("allowed_at") if _day(v.get("allowed_at")) else None,
                         "blocked_at": v.get("blocked_at") if _day(v.get("blocked_at")) else None,
                         "last": v["last"], "day_allowed": _count(v.get("day_allowed")),
                         "day_blocked": _count(v.get("day_blocked"))}
        self._learned, self._stamp = out, stamp
        return out

    @staticmethod
    def learned_verdict(entry: dict | None) -> tuple[str, date] | None:
        """(outcome, its date) from a learned entry: the outcome seen on
        the latest day; both that day: the one counted more that day, a tie
        "blocked" (never all-time counts: an old run of "allowed" can't
        outvote today); else None."""
        if not entry:
            return None
        allowed, blocked = _day(entry.get("allowed_at")), _day(entry.get("blocked_at"))
        if allowed and (not blocked or allowed > blocked):
            return "allowed", allowed
        if blocked and (not allowed or blocked > allowed):
            return "blocked", blocked
        if allowed and blocked:  # the same day (the last one)
            more = _count(entry.get("day_allowed")) > _count(entry.get("day_blocked"))
            return ("allowed" if more else "blocked"), allowed
        return None

    def policy(self, host, own: bool = False) -> str:
        """"allowed", "blocked" or "unknown" for a Canvas host (or URL).
        `own`: the asker's own Canvas host; else what was learned counts
        only once it rests on MIN_EVENTS events (it would tell who uses
        which school)."""
        host = normalize(host)
        if host is None:
            return "unknown"
        with self.lock:
            entry = self._read().get(host)
        if entry and not own and entry["allowed"] + entry["blocked"] < MIN_EVENTS:
            entry = None
        verdict = self.learned_verdict(entry)
        seeded = seed_for(host, self.seed())
        if verdict is not None and (seeded is None or verdict[1] > seeded["checked"]):
            return verdict[0]
        return seeded["student_tokens"] if seeded else "unknown"

    def record(self, host, outcome: str) -> bool:
        """Count one outcome ("allowed" or "blocked") for a host. False
        (nothing written) for a host or outcome that isn't one, or when the
        file can't be written."""
        from kwil.tools.google_auth import write_private

        host = normalize(host)
        if host is None or outcome not in TOKENS:
            return False
        today = self.today().isoformat()
        with self.lock:
            table = {h: dict(v) for h, v in self._read().items()}
            entry = table.get(host) or {"allowed": 0, "blocked": 0, "allowed_at": None,
                                        "blocked_at": None, "last": None}
            if entry["last"] != today:  # a new day: its own counts
                entry["day_allowed"] = entry["day_blocked"] = 0
            entry[outcome] = min(_count(entry[outcome]) + 1, MAX_COUNT)
            entry["day_" + outcome] = min(_count(entry.get("day_" + outcome)) + 1, MAX_COUNT)
            entry[outcome + "_at"] = today
            entry["last"] = today
            table[host] = entry
            if len(table) > MAX_HOSTS:  # the least recently seen go first
                keep = sorted(table, key=lambda h: (table[h]["last"], h == host))[-MAX_HOSTS:]
                table = {h: table[h] for h in keep}
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                write_private(self.path, json.dumps(table, sort_keys=True))
            except OSError as e:
                log.warning("Canvas schools: couldn't save what was learned (%s)",
                            type(e).__name__)
                return False
            self._learned, self._stamp = None, None
        # never the host, at any level: it names the person's school
        log.info("Canvas schools: recorded a %s outcome (%d host(s) known)", outcome, len(table))
        return True
