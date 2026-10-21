"""A family member's or beta tester's own Anthropic API key, from the
phone app's Settings (family.js). Added by webapp.create_app:

  GET    /api/me/anthropic-key   {set, last4, unreadable, required,
                                  available, month_usd?}; never the key
  PUT    /api/me/anthropic-key   {key}: checked with Anthropic (one
                                  models.list call), then saved encrypted
                                  (userkeys.py); 400 a key that is malformed
                                  or refused, 503 when it couldn't be checked
                                  or stored, 429 too many tries
  DELETE /api/me/anthropic-key   removes it

Each answers only about the signed-in person's own key. The owner (WEB_TOKEN)
gets 404: the server's own ANTHROPIC_API_KEY is theirs, and there is no
second, personal one to keep apart from it. PUT costs an Anthropic call, so
it is limited per person (KEY_TRIES_PER_MINUTE). The key is never logged,
echoed or put in an error.
"""

# No `from __future__ import annotations`: FastAPI must see the real
# Request class in the route signatures.
import logging

log = logging.getLogger(__name__)

KEY_TRIES_PER_MINUTE = 5
PATH = "/api/me/anthropic-key"


def register(app, family, error, check=None, limiter=None) -> None:
    """`check(key)`: raises userkeys.KeyProblem for a refused key or a
    failed check (default userkeys.check_key, looked up per call: tests
    replace it). `limiter`: a cloud.RateLimiter keyed by person."""
    from fastapi import Depends, Request
    from starlette.concurrency import run_in_threadpool

    from assistant import userkeys, webapp
    from assistant.cloud import RateLimiter
    from assistant.family import FamilyError

    tries = limiter or RateLimiter(per_minute=KEY_TRIES_PER_MINUTE,
                                   max_tracked=1000)

    def person(session):
        """Their id, or None (the owner, or family accounts off): 404."""
        return session.user if family is not None else None

    @app.get(PATH)
    def key_status(session=Depends(webapp.require_session)):
        uid = person(session)
        if uid is None:
            return error("not found", 404)
        try:
            return family.key_status(uid)
        except FamilyError as e:
            return error(str(e), e.status)

    @app.put(PATH)
    async def save_key(request: Request, session=Depends(webapp.require_session)):
        uid = person(session)
        if uid is None:
            return error("not found", 404)
        if not tries.allow(uid):
            log.warning("User keys: too many key checks from %s", uid)
            return error("too many tries; wait a minute", 429)
        try:
            body = await request.json()
        except ValueError:
            body = {}
        try:
            key = userkeys.clean_key(body.get("key") if isinstance(body, dict) else None)
            problem = family.keys.problem()
            if problem is not None:  # fail closed, before spending a check
                log.warning("User keys: can't store %s's key (%s isn't usable)",
                            uid, userkeys.ENV)
                raise userkeys.KeyProblem(
                    "this server can't store keys yet (its owner needs to set "
                    f"{userkeys.ENV})", 503)
            await run_in_threadpool(check or userkeys.check_key, key)
            saved = await run_in_threadpool(family.set_key, uid, key)
        except userkeys.KeyProblem as e:
            log.info("User keys: %s's key not saved (%d)", uid, e.status)
            return error(str(e), e.status)
        except OSError as e:
            log.error("User keys: saving %s's key failed (%s)", uid, type(e).__name__)
            return error("couldn't save the key; try again", 500)
        if not saved:
            return error("not found", 404)
        return {"set": True, "last4": userkeys.last4(key)}

    @app.delete(PATH)
    async def delete_key(session=Depends(webapp.require_session)):
        uid = person(session)
        if uid is None:
            return error("not found", 404)
        try:
            had = await run_in_threadpool(family.delete_key, uid)
        except OSError:
            return error("couldn't remove the key; try again", 500)
        return {"ok": True, "deleted": had}
