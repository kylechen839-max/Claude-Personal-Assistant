"""HTTP checks against a running web app: python -m assistant.smoke BASE_URL.

Used after the cloud deploy, against the Pi, by the preflight (which
starts `--mode web` on 127.0.0.1 first) and by the watchdog
(assistant/watch.py). Checks /healthz, that a protected route refuses a
request without a session, logs in with the token from an environment
variable (WEB_TOKEN by default; never printed), makes the session a check
session where the server has one (POST /api/check-session: nothing is
saved to history or memory and only read-only tools run), then runs one
text turn and one audio turn, plus, where the server has voice
conversation mode (/api/voice), one hands-free voice turn over the
WebSocket. It signs out at the end.

--conversation (what the watchdog runs) is one realistic multi-turn
conversation in a single session instead (run_conversation): a quick
question (with the router on, the fast model's turn), a text turn that
uses a read-only tool, a text turn that depends on the first, an
audio turn, a hands-free voice turn, and a last text turn while the voice
socket is still open, all of which must answer. It refuses to run on a
server without check sessions, so it never writes anything.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "hey_claude_calendar_tomorrow.wav"
TEXT_QUESTION = "What's on my calendar tomorrow?"
COOKIE = "hc_session"
PROTECTED = "/api/state"


@dataclass
class Outcome:
    name: str
    status: str  # PASS / FAIL / SKIP
    detail: str  # may quote a reply: for the console only, never an alert

    @property
    def reason(self) -> str:
        """What went wrong in a few fixed words (an error class, an HTTP
        status), never text from a reply: safe for logs and alerts."""
        return classify(self.detail) if self.status == "FAIL" else ""


# detail prefix -> the reason it stands for (judge_turn, judge_voice, run_smoke)
REASONS = (("error event", "error event"), ("error reply", "error reply"),
           ("no reply", "empty reply"), ("no usable reply", "empty reply"),
           ("no transcript", "no transcript"), ("no turn_done", "turn never finished"),
           ("no audio chunks", "no audio"), ("expected ready", "voice socket not ready"),
           ("handshake failed", "voice handshake failed"),
           ("forgot", "lost the conversation"), ("no tool", "no tool call"),
           ("refusing", "token would go over http"),
           ("no check session", "no check session"))
_HTTP = re.compile(r"(?:HTTP|->) (\d{3})\b")
_EXCEPTION = re.compile(r"^([A-Z][A-Za-z]*(?:Error|Exception|Timeout|Closed\w*))\b")


# The assistant's own sentences for trouble outside this code (cli.friendly_error):
# a check failing only with these says nothing about the version running.
EXTERNAL = (("Sorry, I can't reach the internet", "no internet"),
            ("Claude is unreachable", "Claude unreachable"),
            ("I'm getting too many requests", "rate limited"),
            ("Sorry, my Anthropic API key", "API key rejected"))
EXTERNAL_REASONS = frozenset(reason for _, reason in EXTERNAL)


def classify(detail: str) -> str:
    """The safe reason for a FAIL detail: one of REASONS' or EXTERNAL's
    fixed phrases, "HTTP <status>", an exception class name, or "failed"."""
    detail = (detail or "").strip()
    m = _EXCEPTION.match(detail)
    if m:
        return m.group(1)
    for prefix, reason in REASONS:
        if detail.startswith(prefix):
            if prefix in ("error event", "error reply", "no usable reply"):
                return next((r for sentence, r in EXTERNAL if sentence in detail), reason)
            return reason
    m = _HTTP.search(detail.split(";")[0])
    return f"HTTP {m.group(1)}" if m else "failed"


class WebClient:
    """requests, with the session cookie kept by hand: it is `Secure`, and
    a cookie jar won't send it back over plain http://127.0.0.1."""

    def __init__(self, base_url: str, timeout: float = 180, session=None):
        import requests

        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.http = session or requests.Session()
        self.cookie: str | None = None

    def _headers(self) -> dict:
        return {"Cookie": f"{COOKIE}={self.cookie}"} if self.cookie else {}

    def get(self, path: str, **kw):
        return self.http.get(self.base + path, headers=self._headers(),
                             timeout=kw.pop("timeout", self.timeout), **kw)

    def post(self, path: str, **kw):
        # never follow a redirect with the body (the token, a recording):
        # requests would re-send it, even from https to http
        return self.http.post(self.base + path, headers=self._headers(),
                              timeout=kw.pop("timeout", self.timeout),
                              allow_redirects=False, **kw)

    def login(self, token: str) -> int:
        r = self.post("/api/login", json={"token": token})
        if r.status_code == 200:
            self.cookie = r.cookies.get(COOKIE) or _cookie_from(r)
        return r.status_code

    def check_session(self) -> tuple[int, dict]:
        """Make this session a check session; (HTTP status, its answer:
        read_only_tools, server_tools)."""
        r = self.post("/api/check-session", json={})
        try:
            body = r.json() if r.status_code == 200 else {}
        except ValueError:
            body = {}
        return r.status_code, body if isinstance(body, dict) else {}

    def logout(self) -> None:
        """Sign out: ends the conversation (a check session's is never
        saved) and frees the server's session slot."""
        if self.cookie:
            self.post("/api/logout", json={}, timeout=30)
            self.cookie = None

    def turn(self, text: str | None = None, wav: Path | None = None) -> list[dict]:
        """Events of one turn (transcript, reply, error, done)."""
        if wav is not None:
            with open(wav, "rb") as f:
                r = self.post("/api/turn", files={"audio": (wav.name, f, "audio/wav")},
                              stream=True)
        else:
            r = self.post("/api/turn", json={"text": text}, stream=True)
        if r.status_code != 200:
            return [{"type": "http", "status": r.status_code}]
        return parse_events(r.iter_lines(decode_unicode=True))


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def insecure_for_token(base_url: str) -> str | None:
    """Why sending the token to `base_url` is unsafe, or None: plain http
    is only for this machine (the preflight's 127.0.0.1 server)."""
    from urllib.parse import urlsplit

    parts = urlsplit(base_url)
    if parts.scheme == "https":
        return None
    if parts.scheme == "http" and (parts.hostname or "").lower() in LOCAL_HOSTS:
        return None
    return (f"refusing to send the token over {parts.scheme or 'no scheme'}:// to "
            f"{parts.hostname or base_url}; use https:// (or --allow-http on a "
            "network you trust, e.g. Tailscale)")


def _cookie_from(response) -> str | None:
    header = response.headers.get("set-cookie", "")
    for part in header.split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE:
            return value
    return None


def parse_events(lines) -> list[dict]:
    """NDJSON lines -> events; stops at "done"."""
    events = []
    for line in lines:
        if not line or not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            events.append({"type": "garbled", "text": line[:80]})
            continue
        events.append(event)
        if event.get("type") == "done":
            break
    return events


def judge_turn(events: list[dict], want_transcript: bool) -> tuple[str, str]:
    """(status, detail) for one turn's events."""
    kinds = [e.get("type") for e in events]
    http = next((e for e in events if e.get("type") == "http"), None)
    if http:
        return "FAIL", f"HTTP {http['status']}"
    errors = [e.get("message", "") for e in events if e.get("type") == "error"]
    if errors:
        return "FAIL", f"error event: {errors[0]}"
    transcript = next((e.get("text", "") for e in events
                       if e.get("type") == "transcript"), "")
    reply = next((e.get("text", "") for e in events if e.get("type") == "reply"), "")
    if want_transcript and not transcript.strip():
        return "FAIL", "no transcript"
    if not reply.strip():
        return "FAIL", f"no reply (events: {kinds})"
    if is_error_reply(reply):
        return "FAIL", f"error reply: {reply}"
    heard = f"heard {transcript!r}; " if want_transcript else ""
    return "PASS", f"{heard}replied {shorten(reply)!r}"


def voice_turn(c: WebClient, wav: Path, timeout: float = 180,
               connect=None, during=None) -> Outcome:
    """One utterance over the voice WebSocket: expects a transcript, reply
    text, at least one audio chunk and turn_done, with no error.
    `during`: called after a good turn while the socket is still open (the
    voice view still on screen), before it ends the voice conversation."""
    import struct
    from urllib.parse import urlsplit

    import numpy as np

    from assistant import audio

    if connect is None:
        from websockets.sync.client import connect
    parts = urlsplit(c.base)
    url = ("wss" if parts.scheme == "https" else "ws") + "://" + parts.netloc \
        + parts.path.rstrip("/") + "/api/voice"
    origin = f"{parts.scheme}://{parts.netloc}"
    samples = np.clip(audio.load_audio(wav), -1, 1)
    pcm = (samples * 32767).astype("<i2").tobytes()
    head = json.dumps({"type": "utterance", "turn": 1,
                       "sample_rate": 16000}).encode()
    frame = struct.pack(">I", len(head)) + head + pcm
    try:
        ws = connect(url, origin=origin, open_timeout=30,
                     additional_headers=c._headers())
    except Exception as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status == 404:
            return Outcome("voice turn", "SKIP", "no /api/voice on this server")
        return Outcome("voice turn", "FAIL", f"handshake failed: {type(e).__name__}: {e}")
    events, chunks = [], 0
    try:
        first = json.loads(ws.recv(timeout=30))
        if first.get("type") != "ready":
            return Outcome("voice turn", "FAIL", f"expected ready, got {first.get('type')}")
        ws.send(frame)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = ws.recv(timeout=max(1.0, deadline - time.monotonic()))
            if isinstance(msg, bytes):
                n = struct.unpack(">I", msg[:4])[0]
                if json.loads(msg[4:4 + n]).get("type") == "audio" and len(msg) > 4 + n:
                    chunks += 1
                continue
            event = json.loads(msg)
            events.append(event)
            if event.get("type") in ("turn_done", "ended"):
                break
        if during is not None and judge_voice(events, chunks)[0] == "PASS":
            during()
        ws.send(json.dumps({"type": "end"}))
    except Exception as e:
        return Outcome("voice turn", "FAIL", f"{type(e).__name__}: {e}")
    finally:
        try:
            ws.close()
        except Exception:
            pass
    return Outcome("voice turn", *judge_voice(events, chunks))


def judge_voice(events: list[dict], audio_chunks: int) -> tuple[str, str]:
    kinds = [e.get("type") for e in events]
    errors = [e.get("message", "") for e in events if e.get("type") == "error"]
    heard = next((e.get("text", "") for e in events if e.get("type") == "transcript"), "")
    reply = "".join(e.get("delta", "") for e in events if e.get("type") == "text")
    if errors:
        return "FAIL", f"error event: {errors[0]}"
    if "turn_done" not in kinds:
        return "FAIL", f"no turn_done (events: {kinds[:8]})"
    if not heard.strip():
        return "FAIL", "no transcript"
    if not reply.strip() or is_error_reply(reply):
        return "FAIL", f"no usable reply ({reply!r})"
    if not audio_chunks:
        return "FAIL", "no audio chunks"
    return "PASS", (f"heard {heard!r}; replied {shorten(reply, 60)!r}; "
                    f"{audio_chunks} audio chunk(s)")


# What cli.get_reply and the tool loop say when something broke.
ERROR_REPLIES = (
    "Sorry, I can't reach the internet right now.",
    "Claude is unreachable right now.",
    "Sorry, my Anthropic API key isn't working.",
    "I'm getting too many requests right now.",
    "Sorry, something went wrong",
    "Sorry, that took too many steps.",
)


def is_error_reply(reply: str) -> bool:
    return any(reply.strip().startswith(e) for e in ERROR_REPLIES)


def shorten(text: str, limit: int = 90) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 3] + "..."


# A read-only tool the check session has -> a question that makes Claude
# use it (the first one present wins). Web search runs on Anthropic's side,
# so the page never sees a "tool" event for it.
TOOL_QUESTIONS = (
    ("list_calendar_events", "What's on my calendar tomorrow?"),
    ("list_assignments", "Which assignments are due this week?"),
    ("ha_list", "Which smart home devices can you see? Just say how many."),
    ("spotify_now_playing", "What's playing on Spotify right now?"),
)
SEARCH_QUESTION = "Search the web: what's the weather in London today? One sentence."
PLAIN_QUESTION = "What is two plus two?"


def run_conversation(c: WebClient, wav: Path, info: dict,
                     voice_ws=voice_turn) -> list[Outcome]:
    """One realistic conversation in this (check) session: what people do.
    `info`: the check session's answer (its read-only tools). Every turn
    must answer; the second and last must still know a number from the
    first, so a turn that loses or breaks the conversation fails (an API
    400 for a malformed history fails every later turn)."""
    import requests

    out: list[Outcome] = []

    def attempt(name, fn):
        try:
            out.append(fn())
        except requests.RequestException as e:
            out.append(Outcome(name, "FAIL", f"{type(e).__name__}: {e}"))

    code = str(1000 + secrets.randbelow(9000))
    tools = list(info.get("read_only_tools") or [])
    want_tool, question = next(((n, q) for n, q in TOOL_QUESTIONS if n in tools),
                               (None, None))
    if question is None:
        question = (SEARCH_QUESTION if "web_search" in (info.get("server_tools") or [])
                    else PLAIN_QUESTION)
    recall = "What number did I ask you to remember? Reply with just the number."

    def remembers(events, name):
        status, detail = judge_turn(events, False)
        reply = next((e.get("text", "") for e in events if e.get("type") == "reply"), "")
        if status == "PASS" and code not in reply:
            return Outcome(name, "FAIL", f"forgot the number from the first turn "
                           f"(replied {shorten(reply)!r})")
        return Outcome(name, status, detail)

    def first():
        events = c.turn(text=f"Please remember the number {code} for this "
                             f"conversation. Also: {question} Keep it short.")
        out.append(Outcome("text turn", *judge_turn(events, False)))
        used = [e.get("name") for e in events if e.get("type") == "tool"]
        if want_tool is None:
            return Outcome("tool call", "SKIP", "no read-only tool the page can see "
                           "(web search runs on Anthropic's side)" if question
                           == SEARCH_QUESTION else "no read-only tool on this server")
        if want_tool in used:
            return Outcome("tool call", "PASS", f"{want_tool} ran (read-only)")
        return Outcome("tool call", "FAIL", f"no tool call: expected {want_tool}, "
                       f"saw {used or 'none'}")

    # Short, with no word that needs the main model: with [assistant] router
    # on, this is the turn the fast model (Haiku) answers. Every later one
    # says "remember", which stays on the main model for the conversation.
    attempt("quick turn", lambda: Outcome("quick turn", *judge_turn(
        c.turn(text=PLAIN_QUESTION), False)))
    attempt("text turn", first)
    attempt("follow-up turn", lambda: remembers(c.turn(text=recall), "follow-up turn"))
    attempt("audio turn", lambda: Outcome("audio turn", *judge_turn(c.turn(wav=wav), True)))
    last = []

    def final_turn():
        attempt("last text turn", lambda: remembers(
            c.turn(text="Thanks. " + recall), "last text turn"))
        last.append(True)

    if voice_ws is not None:
        voice = voice_ws(c, wav, during=final_turn)  # SKIPs itself without /api/voice
        # the voice turn first, then the text turn typed while it was open
        held = [o for o in out if o.name == "last text turn"]
        out[:] = [o for o in out if o.name != "last text turn"] + [voice] + held
    if not last:
        final_turn()  # no voice turn (or it failed): still the last text turn
    return out


def run_smoke(base_url: str, token: str | None, wav: Path = FIXTURE,
              client: WebClient | None = None, health_path: str = "/healthz",
              voice_ws=voice_turn, allow_http: bool = False,
              conversation: bool = False) -> list[Outcome]:
    """All checks against one base URL; never raises for HTTP problems.
    `conversation`: the watchdog's realistic multi-turn conversation
    (run_conversation) instead of the single text, audio and voice turns;
    only in a check session."""
    import requests

    c = client or WebClient(base_url)
    out: list[Outcome] = []

    def attempt(name, fn):
        try:
            out.append(fn())
        except requests.RequestException as e:
            out.append(Outcome(name, "FAIL", f"{type(e).__name__}: {e}"))
        return out[-1].status == "PASS"

    def health():
        r = c.get(health_path, timeout=30)
        return Outcome("healthz", "PASS" if r.status_code == 200 else "FAIL",
                       f"GET {health_path} -> {r.status_code}")

    def refused():
        r = c.get(PROTECTED, timeout=30)
        ok = r.status_code in (401, 403)
        return Outcome("auth required", "PASS" if ok else "FAIL",
                       f"GET {PROTECTED} without a session -> {r.status_code}")

    if not attempt("healthz", health):
        return out
    attempt("auth required", refused)
    if not token:
        out.append(Outcome("login", "SKIP", "no token in the environment"))
        return out
    unsafe = None if allow_http else insecure_for_token(base_url)
    if unsafe:
        out.append(Outcome("login", "FAIL", unsafe))
        return out

    def login():
        code = c.login(token)
        ok = code == 200 and c.cookie
        return Outcome("login", "PASS" if ok else "FAIL",
                       f"POST /api/login -> {code}" + ("" if ok or code != 200
                                                       else " but no session cookie"))

    if not attempt("login", login):
        return out
    try:
        info = check_session(c, out, conversation)
        if info is None:
            return out
        if conversation:
            out.extend(run_conversation(c, wav, info, voice_ws))
            return out

        def text_turn():
            return Outcome("text turn", *judge_turn(c.turn(text=TEXT_QUESTION), False))

        def audio_turn():
            return Outcome("audio turn", *judge_turn(c.turn(wav=wav), True))

        attempt("text turn", text_turn)
        attempt("audio turn", audio_turn)
        if voice_ws is not None:
            out.append(voice_ws(c, wav))  # SKIPs itself without /api/voice
        return out
    finally:
        try:
            c.logout()
        except requests.RequestException:
            pass  # the session ends by itself after conversation_timeout


def check_session(c: WebClient, out: list, required: bool) -> dict | None:
    """Ask for a check session; adds its Outcome. None: stop (it's
    `required` and the server can't, so turns would be saved)."""
    import requests

    try:
        status, info = c.check_session()
    except requests.RequestException as e:
        status, info = type(e).__name__, {}
    if status == 200:
        out.append(Outcome("check session", "PASS", "nothing is saved; read-only "
                           f"tools: {', '.join(info.get('read_only_tools') or []) or 'none'}"))
        return info
    if required:
        out.append(Outcome("check session", "FAIL", f"no check session on this "
                           f"server (-> {status}); not running turns that would "
                           "be saved"))
        return None
    out.append(Outcome("check session", "SKIP", f"-> {status} (an older server: "
                       "these turns are saved as usual)"))
    return {}


def print_table(outcomes, output_fn=print) -> None:
    width = max((len(o.name) for o in outcomes), default=4)
    for o in outcomes:
        output_fn(f"{o.status:<4}  {o.name:<{width}}  {o.detail}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m assistant.smoke",
                                     description="Smoke-check a running web app")
    parser.add_argument("base_url", help="e.g. https://hey-claude.onrender.com")
    parser.add_argument("--token-env", default="WEB_TOKEN",
                        help="environment variable holding the access token")
    parser.add_argument("--audio", default=str(FIXTURE),
                        help="the .wav sent for the audio turn")
    parser.add_argument("--allow-http", action="store_true",
                        help="allow sending the token over plain http:// to "
                             "another machine (only on a network you trust)")
    parser.add_argument("--env-file", default=str(ROOT / ".env"),
                        help="read the token from here if the variable isn't set")
    parser.add_argument("--conversation", action="store_true",
                        help="one realistic multi-turn conversation (text, "
                             "follow-up, audio, hands-free voice, text) in a "
                             "check session, as the watchdog runs it")
    args = parser.parse_args(argv)
    token = os.environ.get(args.token_env)
    if not token and Path(args.env_file).exists():
        from dotenv import dotenv_values

        token = dotenv_values(args.env_file).get(args.token_env)
    from assistant.preflight import Redactor

    redact = Redactor([token or ""])
    extra = {"conversation": True} if args.conversation else {}
    outcomes = run_smoke(args.base_url, token, Path(args.audio),
                         allow_http=args.allow_http, **extra)
    print_table(outcomes, lambda line: print(redact(line)))
    failed = [o for o in outcomes if o.status == "FAIL"]
    print(f"\n{'FAIL' if failed else 'PASS'}: {len(failed)} failed, "
          f"{sum(o.status == 'SKIP' for o in outcomes)} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
