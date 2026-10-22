"""Claude Code hook: remember which sessions are waiting on you.

Claude Code runs this as a hook command (see hooks-snippet.json and the
README) and passes the hook's JSON on stdin. On a `Notification` of type
`permission_prompt`, `idle_prompt` ("Claude is waiting for your input") or
`elicitation_dialog` (an MCP server's question) it writes a marker
`<claude dir>/session-watch/blocked/<session_id>.json`; other types (such
as `auth_success`) are ignored. On `PostToolUse`, `PostToolUseFailure`,
`UserPromptSubmit`, `Stop` or `SessionEnd` it deletes that session's
marker, since you answered or the session moved on.
session_watch.py reads the markers.

It only records. It never answers, approves or denies anything, prints
nothing and always exits 0, so it can't disturb Claude Code. Malformed
input is ignored. The only place it writes is its own state folder
(created 0700, files 0600 on macOS/Linux).

Stdlib only, Python 3.10+, Windows, macOS and Linux.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

STATE_DIR = "session-watch"
MARKER_DIR = "blocked"
SET_EVENTS = {"Notification"}
CLEAR_EVENTS = {"PostToolUse", "PostToolUseFailure", "UserPromptSubmit", "Stop",
                "SessionEnd"}
# Notification types that mean "waiting on you". A payload without the
# field (older Claude Code) still counts; any other type is ignored.
BLOCK_TYPES = ("permission_prompt", "idle_prompt", "elicitation_dialog")
MAX_STDIN = 8 * 1024 * 1024  # a PostToolUse payload carries the tool's output
MAX_MESSAGE = 200
MAX_PATH = 1000
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029\u202a-\u202e\u2066-\u2069]")


def claude_dir(override: str | os.PathLike | None = None) -> Path:
    """`override`, else $CLAUDE_CONFIG_DIR, else ~/.claude."""
    if override:
        return Path(override).expanduser()
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(env).expanduser() if env else Path.home() / ".claude"


def state_dir(root: Path) -> Path:
    return root / STATE_DIR


def safe_id(value: object) -> str | None:
    """The session id if it is safe as a file name, else None."""
    if isinstance(value, str) and _SAFE_ID.fullmatch(value):
        return value
    return None


def clean_text(value: object, limit: int = MAX_MESSAGE) -> str:
    """Untrusted text made printable: control and bidi characters become
    spaces, and it is cut to `limit` characters."""
    if not isinstance(value, str):
        return ""
    return _CONTROL.sub(" ", value).strip()[:limit]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _replace(src: str, dst: Path) -> None:
    # On Windows os.replace fails while another process has dst open.
    for attempt in range(10):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.05 * (attempt + 1))


def private_dir(folder: Path) -> None:
    """Create `folder` and any missing parents, each 0700 on macOS/Linux
    (`mkdir(parents=True, mode=...)` gives the mode to the last one only)."""
    missing = []
    while not folder.is_dir():
        missing.append(folder)
        if folder.parent == folder:
            break
        folder = folder.parent
    for each in reversed(missing):
        try:
            each.mkdir(mode=0o700)
        except FileExistsError:
            pass


def atomic_write_json(path: Path, data: object) -> None:
    """Write `data` as JSON to `path` privately and atomically: a mkstemp
    file in the same folder (0600 on macOS/Linux), then os.replace."""
    private_dir(path.parent)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        _replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def remove(path: Path) -> None:
    for attempt in range(10):
        try:
            path.unlink()
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.05 * (attempt + 1))


def _read_stdin() -> bytes:
    """All of stdin (Claude Code's pipe must be drained), keeping the head."""
    stream = getattr(sys.stdin, "buffer", None)
    if stream is None:
        return b""
    kept = bytearray()
    while True:
        chunk = stream.read(65536)
        if not chunk:
            return bytes(kept)
        if len(kept) < MAX_STDIN:
            kept += chunk[: MAX_STDIN - len(kept)]


def parse(raw: bytes) -> dict:
    """The hook payload. A payload too big to keep whole still yields its
    event name and session id (Claude Code sends those first)."""
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
        return data if isinstance(data, dict) else {}
    except ValueError:
        head = raw[:4096].decode("utf-8", "replace")
        found = {}
        for key in ("session_id", "hook_event_name"):
            m = re.search(r'"%s"\s*:\s*"([A-Za-z0-9_-]{1,128})"' % key, head)
            if m:
                found[key] = m.group(1)
        return found


def handle(payload: dict, root: Path, now: datetime | None = None) -> str | None:
    """Apply one hook event. Returns "set", "cleared" or None (ignored)."""
    sid = safe_id(payload.get("session_id"))
    event = payload.get("hook_event_name")
    if sid is None or not isinstance(event, str):
        return None
    marker = state_dir(root) / MARKER_DIR / f"{sid}.json"
    if event in SET_EVENTS:
        kind = payload.get("notification_type")
        if kind not in (None, "") and kind not in BLOCK_TYPES:
            return None
        atomic_write_json(marker, {
            "session_id": sid,
            "cwd": clean_text(payload.get("cwd"), MAX_PATH),
            "message": clean_text(payload.get("message")),
            "notification_type": clean_text(payload.get("notification_type"), 40),
            "transcript_path": clean_text(payload.get("transcript_path"), MAX_PATH),
            "at": iso(now or utc_now()),
        })
        return "set"
    if event in CLEAR_EVENTS:
        remove(marker)
        return "cleared"
    return None


def main() -> int:
    try:
        handle(parse(_read_stdin()), claude_dir())
    except Exception:  # never disturb Claude Code
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
