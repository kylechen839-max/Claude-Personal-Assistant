"""Beta feedback: what broke, what confused them, what they'd pay. Added by
webapp.create_app (with [family] on):

  POST /api/me/feedback          {kind: "general"|"survey", broke, confused, pay}
                                 -> {ok, id}; each answer at most MAX_ANSWER
                                 characters, the body MAX_BODY bytes, at most
                                 MAX_ENTRIES entries a person, PER_HOUR an hour
  POST /api/me/feedback/dismiss  "not now, not ever" for the one-time survey
  GET  /api/feedback             everyone's feedback, decrypted (owner only;
                                 404 for anyone else)

Each person's entries are kept in `<[family] dir>/<id>/feedback.enc`, sealed
with USER_DATA_KEY under FEEDBACK_LABEL (userkeys.seal), so they go with
their folder when they are removed or delete their account, and are in
"Download my data" (account.py, feedback.json). The owner's own are in
`<[connect] dir>/feedback.enc`. Without USER_DATA_KEY nothing is stored (503).

The one-time survey: /api/state `feedback_due` turns true for a beta tester
`[family] feedback_after_days` after they joined (their first sign-in, else
the invite) or after `feedback_after_conversations` conversations, whichever
comes first (0 turns that trigger off); answering it ("survey") or
dismissing it marks it done in users.json (Family.set_survey), and it is
never due again.

Their words are never logged: INFO has the id, the kind and lengths only.
"""

# No `from __future__ import annotations`: FastAPI must see the real
# Request class in the route signatures (imported inside register()).
import json
import logging
import secrets
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

FEEDBACK_LABEL = b"kwil/feedback/v1"
FILE = "feedback.enc"
OWNER = "owner"                   # the id sealed into the owner's file
KINDS = ("general", "survey")
FIELDS = ("broke", "confused", "pay")
MAX_ANSWER = 2000                 # characters an answer
# bytes a request: three full answers in Chinese (3 bytes a character in
# UTF-8, as the page's JSON.stringify sends them: 18,000) plus the JSON
MAX_BODY = 20 * 1024
MAX_ENTRIES = 50                  # a person
PER_HOUR = 5                      # a person

_lock = threading.Lock()          # one read-modify-write at a time


class FeedbackError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def path_of(family, uid: str | None) -> Path:
    if uid is None:
        return Path(family.config["connect"]["dir"]) / FILE
    return family.dir / uid / FILE


def clean_answer(value) -> str:
    """One answer: text, control characters (but newlines) dropped."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise FeedbackError("answers are text")
    text = "".join(c for c in value if c.isprintable() or c == "\n").strip()
    if len(text) > MAX_ANSWER:
        raise FeedbackError(f"an answer can be at most {MAX_ANSWER} characters")
    return text


def entries(family, uid: str | None) -> list[dict]:
    """Their entries, oldest first ([] if none, or the file doesn't open)."""
    from kwil.userkeys import unseal

    try:
        raw = path_of(family, uid).read_bytes()
    except FileNotFoundError:
        return []
    except OSError as e:
        log.warning("Feedback: %s's file is unreadable (%s)", uid or OWNER, type(e).__name__)
        return []
    data = unseal(FEEDBACK_LABEL, uid or OWNER, raw, family.keys.environ,
                  what="feedback", redo="send it again")
    items = (data or {}).get("entries")
    return [e for e in items if isinstance(e, dict)] if isinstance(items, list) else []


def add(family, uid: str | None, body: dict, clock=time.time) -> dict:
    """Store one entry for `uid` (None = the owner); FeedbackError for a
    bad or over-limit one, userkeys.KeyProblem 503 without USER_DATA_KEY."""
    from kwil.tools.google_auth import write_private
    from kwil.userkeys import seal

    kind = body.get("kind") or "general"
    if kind not in KINDS:
        raise FeedbackError("kind must be general or survey")
    answers = {k: clean_answer(body.get(k)) for k in FIELDS}
    if not any(answers.values()):
        raise FeedbackError("write something first")
    entry = {"id": "fb_" + secrets.token_urlsafe(6), "kind": kind,
             "at": int(clock()), **answers}
    # the family lock (while_listed) too: never written into the folder of
    # someone being removed
    with _lock, family.while_listed(uid or "") as listed:
        if uid is not None and not listed:
            raise FeedbackError("not found", 404)
        found = entries(family, uid)
        if len(found) >= MAX_ENTRIES:
            raise FeedbackError(f"you've sent {MAX_ENTRIES} notes already; thank you!", 409)
        text = seal(FEEDBACK_LABEL, uid or OWNER, {"entries": [*found, entry]},
                    family.keys.environ)  # KeyProblem 503: nothing written
        path = path_of(family, uid)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_private(path, text)
        if kind == "survey" and uid is not None:
            family.set_survey(uid, "answered")
    log.info("Feedback: %s sent %s feedback (%s chars)", uid or OWNER, kind,
             "/".join(str(len(answers[k])) for k in FIELDS))
    return entry


def everyone(family) -> dict:
    """For the owner: {owner: [...], people: [{id, name, tier, entries}]}."""
    people = []
    for user in family.members():
        found = entries(family, user["id"])
        if found:
            people.append({"id": user["id"], "name": user["name"], "tier": user["tier"],
                           "entries": found})
    return {"owner": entries(family, None), "people": people}


def due(family, uid: str | None, history=None, clock=time.time) -> bool:
    """Is their one-time survey due? Only for a listed, active beta tester
    who hasn't answered or dismissed it."""
    from kwil.family import tier_of

    if uid is None or family is None:
        return False
    user = family.get(uid)
    if user is None or user.get("status") != "active" or tier_of(user) != "beta" \
            or user.get("survey"):
        return False
    f = family.config["family"]
    days = float(f.get("feedback_after_days", 0) or 0)
    since = user.get("joined") or user.get("created")
    if days > 0 and isinstance(since, (int, float)) and clock() - since >= days * 86400:
        return True
    convs = int(f.get("feedback_after_conversations", 0) or 0)
    if convs > 0 and history is not None:
        try:
            return history.count(archived=None) >= convs
        except Exception as e:  # noqa: BLE001 - a broken store never breaks /api/state
            log.warning("Feedback: counting %s's conversations failed (%s)", uid,
                        type(e).__name__)
    return False


def register(app, family, error, limiter=None) -> None:
    """The routes in the module doc; all 404 with [family] off."""
    from fastapi import Depends, Request
    from starlette.concurrency import run_in_threadpool

    from kwil import webapp
    from kwil.cloud import RateLimiter
    from kwil.userkeys import KeyProblem

    sends = limiter or RateLimiter(per_minute=PER_HOUR, window=3600, max_tracked=1000)

    @app.post("/api/me/feedback")
    async def send(request: Request, session=Depends(webapp.require_session)):
        if family is None:
            return error("not found", 404)
        raw = await request.body()
        if len(raw) > MAX_BODY:
            return error("that's too long", 413)
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return error("send JSON", 400)
        who = session.user or OWNER
        if not sends.allow(who):
            log.warning("Feedback: too many notes from %s", who)
            return error("thanks! that's plenty for this hour", 429)
        try:
            entry = await run_in_threadpool(add, family, session.user, body)
        except FeedbackError as e:
            return error(str(e), e.status)
        except KeyProblem as e:
            log.warning("Feedback: can't store %s's note (%d)", who, e.status)
            return error("feedback can't be stored on this server yet", e.status)
        except OSError as e:
            log.error("Feedback: saving %s's note failed (%s)", who, type(e).__name__)
            return error("couldn't save it; try again", 500)
        return {"ok": True, "id": entry["id"]}

    @app.post("/api/me/feedback/dismiss")
    async def dismiss(session=Depends(webapp.require_session)):
        if family is None or session.user is None:
            return error("not found", 404)
        try:
            done = await run_in_threadpool(family.set_survey, session.user, "dismissed")
        except OSError:
            return error("couldn't save that; try again", 500)
        if not done:
            return error("not found", 404)
        log.info("Feedback: %s dismissed the survey", session.user)
        return {"ok": True}

    @app.get("/api/feedback")
    def all_feedback(session=Depends(webapp.require_owner)):
        if family is None:
            return error("not found", 404)
        return everyone(family)
