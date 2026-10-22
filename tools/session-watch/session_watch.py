"""Find local Claude Code sessions that are waiting on you, and tell you.

Two sources, merged by session id (the hook wins, except that a
transcript `security_denied` block beats an `idle_prompt` marker):
- hook markers that notify_hook.py writes on a Claude Code `Notification`
  (`<claude dir>/session-watch/blocked/<session_id>.json`);
- a transcript check that needs no hook: the newest `tool_use` in a recent
  `<claude dir>/projects/*/*.jsonl` (or in one of that session's subagent
  transcripts, `<session_id>/subagents/*.jsonl`) has no `tool_result` yet
  and the session has been quiet for `--threshold` seconds, which is what
  an approval prompt looks like. Each transcript is judged on its own
  mtime; only an open `Agent`/`Task` call counts its subagents' writes as
  activity. The same check reports a session that stopped after a security
  check (classifier, permission rule, PreToolUse hook) denied a tool call
  (`notification_type` "security_denied").

It only reports: it never answers, approves or denies a prompt and never
changes Claude Code's settings. It reads transcript tails locally and
writes only under `<claude dir>/session-watch/` (and nothing at all with
`--no-notify`). Each new block gets one desktop notification and one line
in watch.log (ids, project, wait and source; never transcript text).

    python session_watch.py --once --json --no-notify

Stdlib only, Python 3.10+, Windows, macOS and Linux.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import notify_hook
from notify_hook import atomic_write_json, clean_text, iso, private_dir, safe_id, state_dir

log = logging.getLogger("session_watch")

DEFAULT_THRESHOLD = 120
RECENT = 24 * 3600  # transcripts and markers older than this are ignored
TAIL_BYTES = 64 * 1024
MAX_MARKER_BYTES = 64 * 1024
NOTIFIED_DAYS = 7
MAX_NOTIFIED = 2000
MAX_LOG_BYTES = 1024 * 1024
MAX_SUBAGENTS = 8  # newest subagent transcripts checked per session
TITLE = "Claude Code needs you"
STATE_ERROR = 3  # --once exit code when the state folder can't be written
# Windows toast. Fixed text: the title and body arrive in environment
# variables, so nothing from a session is ever part of the script.
WINDOWS_TOAST = (
    "$ErrorActionPreference = 'Stop'; "
    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications,"
    " ContentType = WindowsRuntime] > $null; "
    "$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
    "[Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
    "$t = $x.GetElementsByTagName('text'); "
    "$t.Item(0).AppendChild($x.CreateTextNode($env:SESSION_WATCH_TITLE)) > $null; "
    "$t.Item(1).AppendChild($x.CreateTextNode($env:SESSION_WATCH_BODY)) > $null; "
    "$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe'; "
    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show("
    "[Windows.UI.Notifications.ToastNotification]::new($x))"
)
MAC_SCRIPT = ["-e", "on run argv",
              "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
              "-e", "end run"]


# ---------------------------------------------------------------- transcripts

def _tail_lines(path: Path, limit: int = TAIL_BYTES) -> list[bytes]:
    with open(path, "rb") as f:
        size = f.seek(0, os.SEEK_END)
        f.seek(max(0, size - limit))
        data = f.read(limit)
    lines = data.splitlines()
    if size > limit and lines:
        lines = lines[1:]  # the first line was cut in the middle
    return lines


def _blocks(entry: dict) -> list:
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def pending_tool(lines: list[bytes]) -> str | None:
    """The tool name of a call in the newest assistant message with tool
    calls that has no `tool_result` yet, else None.

    Claude Code writes one content block per transcript entry, so parallel
    calls arrive as several entries sharing `message.id`; they are grouped
    by that id (an entry without one is its own message)."""
    last_ids: dict[str, str] = {}
    last_message: str | None = None
    answered: set[str] = set()
    for raw in lines:
        try:
            entry = json.loads(raw)
        except (ValueError, RecursionError):
            continue
        if not isinstance(entry, dict):
            continue
        kind = entry.get("type")
        if kind == "assistant":
            uses = {b["id"]: b.get("name") for b in _blocks(entry)
                    if b.get("type") == "tool_use" and isinstance(b.get("id"), str)}
            if not uses:
                continue
            message_id = entry["message"].get("id")
            if not isinstance(message_id, str):
                message_id = None
            if message_id is not None and message_id == last_message:
                last_ids.update(uses)  # another parallel call of the same message
            else:
                last_ids, answered = uses, set()
            last_message = message_id
        elif kind == "user":
            answered.update(b["tool_use_id"] for b in _blocks(entry)
                            if b.get("type") == "tool_result"
                            and isinstance(b.get("tool_use_id"), str))
    for tool_id, name in last_ids.items():
        if tool_id not in answered:
            return name if isinstance(name, str) else ""
    return None


def _subagents(path: Path) -> list[tuple[float, Path]]:
    """(mtime, path) of the session's subagent transcripts, newest first."""
    found = []
    for sub in (path.parent / path.stem / "subagents").glob("*.jsonl"):
        try:
            found.append((sub.stat().st_mtime, sub))
        except OSError:
            continue
    return sorted(found, reverse=True)


# A tool_result with is_error whose text (leading whitespace stripped,
# case-insensitive) starts with one of these is a security check stopping a
# call. They are anchored to the start because Claude Code's own denial
# texts start the content, while a failing command's output starts with
# "Exit code N" and may quote anything (a git 403 "Permission to user-x
# denied", a pre-commit hook "blocked by ... hook").
DENIAL_PATTERNS = tuple(re.compile(p, re.DOTALL) for p in (
    # the auto mode classifier ("Permission for this action was denied ..."),
    # and the built-in safety check on dangerous commands ("Permission for
    # this command was denied by a built-in Claude Code safety check ...")
    r"permission for this (?:action|command) (?:was|has been) denied\b",
    # a permission deny rule: "Permission to use Bash with command ... has
    # been denied.", "Permission to read <path> has been denied.",
    # "Permission to edit <path> has been denied." The verb is a whole word
    # followed by a space, so "\S+ " can end in only one place and the lazy
    # scan for "has been denied" runs once: linear, not quadratic, on the
    # DENIAL_HEAD characters.
    r"permission to (?:use|read|edit) \S+ .*?\bhas been denied\b",
    # an MCP tool turned off ("MCP tool <server>/<tool> has been denied")
    r"mcp tool \S+ (?:was|has been) denied\b",
    # a PreToolUse hook that blocked the call: exit code 2 ("PreToolUse:Bash
    # hook error: ...") or a JSON deny without a reason ("Hook
    # PreToolUse:Bash denied this tool"). A JSON deny WITH a
    # permissionDecisionReason shows only the hook's own free text, which
    # is deliberately not matched (see README "Security-check stops").
    r"(?:hook )?pretooluse:",
))
# Kyle saying no himself is his choice, not a security check.
USER_REJECTIONS = ("the user doesn't want to proceed", "user rejected",
                   "the user doesn't want to take this action")
DENIAL_HEAD = 2000  # only the start of an error result is matched (a deny
# rule quotes the whole command before "has been denied")
SECURITY_DENIED = "security_denied"


def _result_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content[:DENIAL_HEAD]
    if isinstance(content, list):
        parts = [b["text"] for b in content
                 if isinstance(b, dict) and isinstance(b.get("text"), str)]
        return " ".join(parts)[:DENIAL_HEAD]
    return ""


def is_denial(block: dict) -> bool:
    """A tool_result block where a security check refused the call."""
    if block.get("is_error") is not True:
        return False
    text = _result_text(block).lower()
    if any(r in text for r in USER_REJECTIONS):
        return False
    text = text.lstrip()
    return any(p.match(text) for p in DENIAL_PATTERNS)


def denied_tool(lines: list[bytes]) -> tuple[str, str] | None:
    """(tool_use id, tool name) of the newest call a security check denied
    when nothing worked after it: no later successful tool_result and no new
    message from Kyle. Else None."""
    names: dict[str, str] = {}
    denied: tuple[str, str] | None = None
    for raw in lines:
        try:
            entry = json.loads(raw)
        except (ValueError, RecursionError):
            continue
        if not isinstance(entry, dict):
            continue
        kind = entry.get("type")
        if kind == "assistant":
            for b in _blocks(entry):
                if b.get("type") == "tool_use" and isinstance(b.get("id"), str):
                    name = b.get("name")
                    names[b["id"]] = name if isinstance(name, str) else ""
        elif kind == "user":
            message = entry.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            results = [b for b in _blocks(entry) if b.get("type") == "tool_result"
                       and isinstance(b.get("tool_use_id"), str)]
            if not results:
                if entry.get("isMeta") is not True and (isinstance(content, str)
                                                        or _blocks(entry)):
                    denied = None  # Kyle wrote again: he has seen it
                continue
            for b in results:
                if is_denial(b):
                    denied = (b["tool_use_id"], names.get(b["tool_use_id"], ""))
                elif b.get("is_error") is not True:
                    denied = None  # carried on and something worked
    return denied


AGENT_TOOLS = ("Agent", "Task")  # a call that runs a subagent (Task is the old name)


def _quiet(since: float, threshold: float, now: float) -> bool:
    return threshold <= now - since <= RECENT


def _state(lines: list[bytes]) -> tuple[str, str, str] | None:
    """("prompt", tool, "") for an open call, ("denied", tool, tool_use id)
    for a call a security check stopped with nothing done since, else None."""
    tool = pending_tool(lines)
    if tool is not None:
        return ("prompt", tool, "")
    denied = denied_tool(lines)
    return ("denied", denied[1], denied[0]) if denied else None


def _block(sid: str, path: Path, state: tuple[str, str, str], since: float,
           now: float, where: str) -> dict:
    kind, tool, tool_id = state
    if kind == "denied":
        reason = f"stopped after a security check denied a tool call{where}"
        key = f"{sid}|denied|{clean_text(tool_id, 128)}"
    else:
        reason = f"waiting (probably an approval prompt{where})"
        key = f"{sid}|transcript|{int(since)}"
    return {
        "session_id": sid,
        "project": clean_text(path.parent.name, 200),
        "source": "transcript",
        "since": iso(datetime.fromtimestamp(since, timezone.utc)),
        "waiting_seconds": int(now - since),
        "reason": reason,
        "tool": clean_text(tool, 64) or None,
        "notification_type": SECURITY_DENIED if kind == "denied" else None,
        "message": None,
        "key": key,
    }


def _check_session(path: Path, threshold: float, now: float) -> dict | None:
    """Each transcript is judged on its own mtime, so a busy background
    agent never hides a prompt in the main session. Only a main call that
    runs a subagent (`Agent`/`Task`) counts the subagents' writes as its own
    activity: that call stays open while its agent works."""
    sid = safe_id(path.stem)
    if sid is None:
        return None
    mtime = path.stat().st_mtime
    if now - mtime > RECENT:
        return None  # too old (subagents only write while it is live)
    subs = _subagents(path)
    # A prompt or a denial inside a subagent that wrote since the main
    # transcript last moved (a running foreground or background agent).
    # Older ones finished, or died with a session that has since moved on.
    for sub_mtime, sub in subs[:MAX_SUBAGENTS]:
        if sub_mtime < mtime:
            break
        if not _quiet(sub_mtime, threshold, now):
            continue  # busy
        state = _state(_tail_lines(sub))
        if state is not None:
            return _block(sid, path, state, sub_mtime, now, " in a subagent")
    state = _state(_tail_lines(path))
    if state is None:
        return None
    since = mtime
    if state[0] == "prompt" and state[1] in AGENT_TOOLS:
        since = max([mtime] + [m for m, _ in subs[:1]])
    if not _quiet(since, threshold, now):
        return None  # busy (maybe in a subagent)
    return _block(sid, path, state, since, now, "")


def scan_transcripts(root: Path, threshold: float, now: float | None = None) -> list[dict]:
    """Sessions whose transcript (or a subagent's) ends on an unanswered tool
    call while the session has been quiet for `threshold` seconds (but
    changed within the last 24 h). One bad file never stops the scan."""
    now = time.time() if now is None else now
    found = []
    for path in sorted((root / "projects").glob("*/*.jsonl")):
        try:
            block = _check_session(path, threshold, now)
        except Exception as e:  # noqa: BLE001 - skip the file, keep scanning
            log.debug("skipped a transcript: %s", type(e).__name__)
            continue
        if block is not None:
            found.append(block)
    return found


# -------------------------------------------------------------------- markers

def _parse_at(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def read_markers(root: Path, now: float | None = None, prune: bool = False) -> list[dict]:
    """Blocks the Notification hook recorded in the last 24 h. With `prune`,
    markers older than that are deleted."""
    now = time.time() if now is None else now
    found = []
    for path in sorted((state_dir(root) / notify_hook.MARKER_DIR).glob("*.json")):
        try:
            block = _read_marker(path, now, prune)
        except Exception as e:  # noqa: BLE001 - skip the marker, keep scanning
            log.debug("skipped a marker: %s", type(e).__name__)
            continue
        if block is not None:
            found.append(block)
    return found


REASONS = {
    "permission_prompt": "approval prompt (Claude Code notification)",
    "idle_prompt": "idle, waiting for your next message (Claude Code notification)",
    "elicitation_dialog": "waiting for your answer to a question (Claude Code notification)",
}


def _read_marker(path: Path, now: float, prune: bool) -> dict | None:
    try:
        if path.stat().st_size > MAX_MARKER_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    sid = safe_id(data.get("session_id"))
    at = _parse_at(data.get("at"))
    if sid != path.stem or at is None:
        return None
    age = now - at.timestamp()
    if age > RECENT:
        if prune:
            try:
                notify_hook.remove(path)
            except OSError:
                pass
        return None
    kind = data.get("notification_type")
    kind = kind if kind in notify_hook.BLOCK_TYPES else None
    cwd = clean_text(data.get("cwd"), notify_hook.MAX_PATH)
    transcript = clean_text(data.get("transcript_path"), notify_hook.MAX_PATH)
    if cwd:
        project = Path(cwd.replace("\\", "/")).name
    elif transcript:
        project = Path(transcript.replace("\\", "/")).parent.name
    else:
        project = ""
    return {
        "session_id": sid,
        "project": clean_text(project, 200),
        "source": "hook",
        "since": iso(at),
        "waiting_seconds": max(0, int(age)),
        "reason": REASONS.get(kind, "waiting for you (Claude Code notification)"),
        "tool": None,
        "notification_type": kind,
        "message": clean_text(data.get("message")) or None,
        "key": f"{sid}|hook|{iso(at)}",
    }


# Hook marker types a transcript security_denied block beats: after a
# denial the turn ends, Stop clears the marker and about a minute later
# Claude Code sends idle_prompt, before the transcript check (--threshold)
# can report the denial. An approval prompt or a question still wins.
DENIAL_BEATS = ("idle_prompt",)


def merge(hook: list[dict], heuristic: list[dict]) -> list[dict]:
    """One entry per session; a hook marker beats the transcript check,
    except that a security_denied block beats an idle_prompt marker.
    Longest wait first."""
    by_id = {b["session_id"]: b for b in heuristic}
    for b in hook:
        other = by_id.get(b["session_id"])
        if (other is not None and other["notification_type"] == SECURITY_DENIED
                and b["notification_type"] in DENIAL_BEATS):
            continue
        by_id[b["session_id"]] = b
    return sorted(by_id.values(), key=lambda b: (-b["waiting_seconds"], b["session_id"]))


# ------------------------------------------------------------- notifications

def _notifier(title: str, body: str) -> tuple[list[str], dict | None]:
    """The command (an argument list, never a shell string) and extra env."""
    if sys.platform == "darwin":
        return ["osascript", *MAC_SCRIPT, title, body], None
    if sys.platform == "win32":
        env = dict(os.environ, SESSION_WATCH_TITLE=title, SESSION_WATCH_BODY=body)
        return (["powershell.exe", "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-Command", WINDOWS_TOAST], env)
    return ["notify-send", "--", title, body], None


def desktop_notify(title: str, body: str) -> bool:
    cmd, env = _notifier(title, body)
    try:
        r = subprocess.run(cmd, env=env, capture_output=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("desktop notification failed (%s): %s", cmd[0], type(e).__name__)
        return False
    if r.returncode != 0:
        log.warning("desktop notification failed (%s): exit %s", cmd[0], r.returncode)
        return False
    return True


KINDS = {"permission_prompt": "approval", "idle_prompt": "idle",
         "elicitation_dialog": "question", SECURITY_DENIED: "security check"}


def describe(block: dict) -> str:
    minutes = block["waiting_seconds"] // 60
    wait = f"{minutes} min" if minutes else f"{block['waiting_seconds']} s"
    if block["notification_type"] == SECURITY_DENIED:
        what = f"{block['tool'] or 'a tool call'} was denied by a security check"
    else:
        what = block["message"] or (f"{block['tool']} is waiting" if block["tool"]
                                    else "waiting for you")
    kind = KINDS.get(block["notification_type"])
    source = f"{block['source']}, {kind}" if kind else block["source"]
    return f"{block['project'] or block['session_id']}: {what} ({wait}, {source})"


# ---------------------------------------------------------------------- state

def load_notified(root: Path) -> dict[str, float]:
    try:
        data = json.loads((state_dir(root) / "notified.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items()
            if isinstance(k, str) and isinstance(v, (int, float))}


def save_notified(root: Path, notified: dict[str, float], now: float) -> None:
    cutoff = now - NOTIFIED_DAYS * 86400
    kept = sorted(((k, v) for k, v in notified.items() if v >= cutoff),
                  key=lambda kv: kv[1])[-MAX_NOTIFIED:]
    atomic_write_json(state_dir(root) / "notified.json", dict(kept))


def append_log(root: Path, block: dict, now: float) -> None:
    """One line per new block: ids, project, wait and source only."""
    path = state_dir(root) / "watch.log"
    private_dir(path.parent)
    try:
        if path.stat().st_size > MAX_LOG_BYTES:
            os.replace(path, path.with_name("watch.log.1"))
    except OSError:
        pass
    line = (f"{iso(datetime.fromtimestamp(now, timezone.utc))} blocked"
            f" session={block['session_id']} project={block['project'] or '-'}"
            f" waiting={block['waiting_seconds']}s source={block['source']}\n")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(line)


# ----------------------------------------------------------------------- scan

class StateError(Exception):
    """The state folder couldn't be written. `blocked` is the scan's result;
    no notification went out for anything that wasn't saved first."""

    def __init__(self, blocked: list[dict], error: OSError):
        super().__init__(type(error).__name__)
        self.blocked = blocked


def scan(root: Path, threshold: float = DEFAULT_THRESHOLD, notify: bool = True,
         now: float | None = None) -> list[dict]:
    """Blocked sessions, each with `new` (not notified before). With
    `notify`, new ones get a notification and a watch.log line; without it
    nothing is written. notified.json is saved before any notification goes
    out, so a folder that can't be written raises StateError and never
    turns into a notification on every scan."""
    now = time.time() if now is None else now
    blocked = merge(read_markers(root, now, prune=notify),
                    scan_transcripts(root, threshold, now))
    notified = load_notified(root)
    for block in blocked:
        block["new"] = block["key"] not in notified
    if not notify:
        return blocked
    fresh = [b for b in blocked if b["new"]]
    for block in fresh:
        notified[block["key"]] = now
    try:
        save_notified(root, notified, now)
    except OSError as e:
        log.warning("can't save state in %s (%s); no notification sent",
                    state_dir(root), type(e).__name__)
        raise StateError(blocked, e) from e
    failed: OSError | None = None
    for block in fresh:
        log.info("new blocked session %s (%s, %s s)", block["session_id"],
                 block["source"], block["waiting_seconds"])
        log.debug("notification text: %s", block["message"])
        desktop_notify(TITLE, describe(block))
        try:
            append_log(root, block, now)
        except OSError as e:
            failed = e
    if failed is not None:
        log.warning("can't write watch.log in %s (%s)", state_dir(root),
                    type(failed).__name__)
        raise StateError(blocked, failed)
    return blocked


def public(blocked: list[dict]) -> list[dict]:
    """The stable --json shape."""
    keys = ("session_id", "project", "source", "since", "waiting_seconds",
            "reason", "tool", "notification_type", "message", "new")
    return [{k: b[k] for k in keys} for b in blocked]


def render(blocked: list[dict]) -> str:
    if not blocked:
        return "No blocked Claude Code sessions."
    return "\n".join(f"{b['session_id']}  {describe(b)}" for b in blocked)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Report local Claude Code sessions that are "
                                "waiting on you. Read-only towards Claude Code.")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="scan once (the default)")
    mode.add_argument("--interval", type=float, metavar="SECONDS",
                      help="scan every SECONDS until stopped")
    p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, metavar="SECONDS",
                   help="quiet time before an unanswered tool call counts (default 120)")
    p.add_argument("--claude-dir", metavar="PATH",
                   help="Claude Code's folder (default $CLAUDE_CONFIG_DIR or ~/.claude)")
    p.add_argument("--json", action="store_true", help="print the blocked sessions as JSON")
    p.add_argument("--no-notify", action="store_true",
                   help="no desktop notification and no writes at all")
    args = p.parse_args(argv)
    if args.interval is not None and args.interval < 1:
        p.error("--interval must be at least 1 second")
    if args.threshold < 0:
        p.error("--threshold must not be negative")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="session_watch: %(message)s")
    root = notify_hook.claude_dir(args.claude_dir)
    try:
        while True:
            code = 0
            try:
                blocked = scan(root, args.threshold, notify=not args.no_notify)
            except StateError as e:
                blocked, code = e.blocked, STATE_ERROR
            except Exception as e:  # noqa: BLE001
                if args.interval is None:
                    raise
                log.warning("scan failed (%s); trying again next time", type(e).__name__)
                time.sleep(args.interval)
                continue
            if args.json:
                print(json.dumps(public(blocked), sort_keys=True), flush=True)
            else:
                print(render(blocked), flush=True)
            if args.interval is None:
                return code  # scan() already logged the one-line warning
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
