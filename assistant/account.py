"""Account self-service for invited people (family.py): "Download my data"
and "Delete my account" in their own Settings (web/family.js).

  GET  /api/me/export   a zip of their conversations (history.export JSON)
                        and profile; never tokens or keys
  POST /api/me/delete   {"confirm": "DELETE" or their name}: revoke their
                        Google grant (best effort), then Family.remove

Both are for the signed-in person's own session only: there is no id in
the path or body, so nobody can name someone else's account. The owner
(WEB_TOKEN, a bearer or failover access token) gets 403: Kyle's data is the
whole server, and he removes people from the invite list instead.

Deleting reuses Family.remove: it drops them from users.json, signs out
every session of theirs (cookies, live voice calls) and deletes
`<dir>/<id>/`: history, Google token, and every other per-person file
(their own API key, Notion token), with its retry for files locked on
Windows. Google is told first (POST https://oauth2.googleapis.com/revoke,
the token in the body, never the URL), so the grant doesn't outlive the
account; a failure there never blocks the delete and is logged without
the token.

An export is built in a private folder under the family data dir
(`<dir>/.exports/export-<id>.*`, mkdtemp, 0700; `.exports` can never be a
person's id), streamed, then removed when the response ends, however it
ends (ExportResponse), including a client that leaves before the body
starts. Deleting the account also removes their export folders, and
folders older than STALE are swept at startup and on every export.
Bounded: [family] export_max_mb of content (before compression), one
export at a time per person, EXPORTS_PER_HOUR per person. Logs carry
ids and sizes only, never names, titles, text or tokens.
"""

# No `from __future__ import annotations`: FastAPI must see the real
# Request class in the route signatures (imported inside register()).
import json
import logging
import os
import shutil
import tempfile
import threading
import time
import zipfile
from collections import deque
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

REVOKE_URL = "https://oauth2.googleapis.com/revoke"
CONFIRM_WORD = "DELETE"
EXPORTS_PER_HOUR = 3
RATE_WINDOW = 3600.0
STALE = 600.0          # an export (or its temp folder) older than this is abandoned
CHUNK = 64 * 1024
PAGE = 200             # conversations read per history.list call
EXPORT_PREFIX = "export-"
EXPORTS_DIR = ".exports"  # in the family dir; never a valid id (family._ID)


class ExportError(Exception):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def revoke_google(token: str, timeout: float = 5.0) -> None:
    """Ask Google to revoke a grant (a refresh token revokes it all). The
    token goes in the form body, never the URL. Raises on any failure."""
    import requests

    r = requests.post(REVOKE_URL, data={"token": token}, timeout=timeout,
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")


def confirmed(given, user: dict) -> bool:
    """The typed confirmation: DELETE, or their account name (any case)."""
    if not isinstance(given, str) or len(given) > 200:
        return False
    given = " ".join(given.split())
    name = " ".join(str(user.get("name") or "").split())
    return given == CONFIRM_WORD or bool(name) and given.casefold() == name.casefold()


def profile(user: dict) -> dict:
    """What the export says about them: never the invite hash, tokens or keys."""
    from assistant.family import tier_of

    created = user.get("created")
    return {"id": user.get("id"), "name": user.get("name"),
            "language": user.get("language"), "timezone": user.get("timezone") or "",
            "tier": tier_of(user),
            "created": (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(created))
                        if isinstance(created, (int, float)) else None)}


class Account:
    """The export and delete logic, separate from the routes so tests can
    swap `revoke`, `clock` and `tmp_root`. Sweeps stale export folders
    when it's made (at startup)."""

    def __init__(self, family, revoke: Callable[[str], None] = revoke_google,
                 clock=time.time, tmp_root: str | Path | None = None):
        self.family = family
        self.revoke = revoke
        self.clock = clock
        self.tmp_root = Path(tmp_root) if tmp_root else family.dir / EXPORTS_DIR
        self.lock = threading.Lock()
        self._busy: dict[str, float] = {}        # uid -> when their export started
        self._folders: dict[str, Path] = {}      # uid -> their export's folder
        self._recent: dict[str, deque] = {}      # uid -> export start times
        self._sweep()

    @property
    def max_bytes(self) -> int:
        return int(float(self.family.config["family"]["export_max_mb"]) * 1_000_000)

    # ---------- delete ----------
    def revoke_token(self, uid: str) -> None:
        """Best effort: their Google grant, before the folder (and the token
        file) goes. Never raises; never logs the token."""
        from assistant.tools.google_auth import load_token_info

        try:  # environ={}: their file only, never GOOGLE_TOKEN_JSON (Kyle's)
            info = load_token_info(self.family.google_token(uid), environ={})
        except (OSError, ValueError):
            info = None
        token = (info or {}).get("refresh_token") or (info or {}).get("token")
        if not token:
            return
        try:
            self.revoke(token)
            log.info("Account: revoked %s's Google grant", uid)
        except Exception as e:  # noqa: BLE001 - deleting goes ahead regardless
            log.warning("Account: revoking %s's Google grant failed (%s); "
                        "deleting anyway", uid, type(e).__name__)

    def delete(self, uid: str, sign_out: Callable[[str], None]) -> bool:
        """Revoke, then Family.remove (sign out, delete the folder). False
        if they weren't listed. OSError: users.json couldn't be saved."""
        if self.family.get(uid) is None:
            return False
        self.revoke_token(uid)
        removed = self.family.remove(uid, sign_out)
        with self.lock:
            self._recent.pop(uid, None)
            self._busy.pop(uid, None)
            self._folders.pop(uid, None)
        self._remove_exports(uid)  # a copy must not outlive the account
        if removed:
            log.info("Account: %s deleted their account", uid)
        return removed

    # ---------- export ----------
    def _start(self, uid: str) -> None:
        now = self.clock()
        with self.lock:
            started = self._busy.get(uid)
            if started is not None and now - started < STALE:
                raise ExportError("an export is already being made; wait for it", 409)
            recent = self._recent.setdefault(uid, deque())
            while recent and now - recent[0] > RATE_WINDOW:
                recent.popleft()
            if len(recent) >= EXPORTS_PER_HOUR:
                raise ExportError("too many downloads; try again in an hour", 429)
            recent.append(now)
            self._busy[uid] = now
            # bounded: only people still listed keep an entry
            for other in [k for k in self._recent if k != uid
                          and (not self._recent[k]
                               or now - self._recent[k][-1] > RATE_WINDOW)]:
                del self._recent[other]

    def _done(self, uid: str, folder: Path | None = None) -> None:
        """Free their export slot (only this export's: a newer one, started
        after this one went stale, keeps its own)."""
        with self.lock:
            if folder is None or self._folders.get(uid) in (None, folder):
                self._busy.pop(uid, None)
                self._folders.pop(uid, None)

    def finish(self, uid: str, folder: Path) -> None:
        """The download ended (sent, dropped or never started): remove the
        folder and free the slot. Idempotent."""
        shutil.rmtree(folder, ignore_errors=True)
        self._done(uid, folder)

    def _remove_exports(self, uid: str) -> None:
        """Every export folder of theirs, this run's or an earlier one's
        (`export-<id>.<random>`: "." is in neither an id nor mkdtemp's
        suffix, so one id's glob never matches another's)."""
        if not self.tmp_root.is_dir():
            return
        for folder in self.tmp_root.glob(f"{EXPORT_PREFIX}{uid}.*"):
            shutil.rmtree(folder, ignore_errors=True)

    def _sweep(self) -> None:
        """Remove export folders a dropped download (or a crash) left behind."""
        if not self.tmp_root.is_dir():
            return
        now = time.time()
        for folder in self.tmp_root.glob(EXPORT_PREFIX + "*"):
            try:
                if now - folder.stat().st_mtime > STALE:
                    shutil.rmtree(folder, ignore_errors=True)
            except OSError:
                pass

    def _root(self) -> Path:
        """The private exports folder (0700; chmod does nothing on Windows,
        where the family dir's own permissions apply)."""
        self.tmp_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            os.chmod(self.tmp_root, 0o700)
        return self.tmp_root

    def build(self, uid: str) -> tuple[Path, Path]:
        """(temp folder, zip in it). The caller streams the zip and calls
        finish(); on an error here everything is already cleaned up."""
        user = self.family.get(uid)
        ctx = self.family.context(uid)
        if user is None or ctx is None:
            raise ExportError("not signed in", 401)
        self._start(uid)
        try:
            self._sweep()
            folder = Path(tempfile.mkdtemp(prefix=f"{EXPORT_PREFIX}{uid}.",
                                           dir=self._root()))
        except BaseException:
            self._done(uid)
            raise
        with self.lock:
            self._folders[uid] = folder
        try:
            path = folder / "hey-claude-data.zip"
            count, size = self._write(path, user, ctx.history)
        except BaseException:
            self.finish(uid, folder)
            raise
        log.info("Account: %s exported %d conversation(s), %d bytes", uid, count, size)
        return folder, path

    def _write(self, path: Path, user: dict, store) -> tuple[int, int]:
        from assistant import history

        cap, total = self.max_bytes, 0

        def add(zf, name: str, text: str) -> None:
            nonlocal total
            data = text.encode("utf-8")
            total += len(data)
            if total > cap:
                raise ExportError(
                    f"your data is larger than {cap // 1_000_000} MB; ask the "
                    "person who invited you for a copy", 413)
            zf.writestr(name, data)

        count = 0
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            add(zf, "profile.json", json.dumps(profile(user), indent=2,
                                               ensure_ascii=False))
            offset = 0
            while store is not None:
                page = store.list(limit=PAGE, offset=offset, archived=None)
                for item in page:
                    try:
                        conv = store.get(item["id"])
                    except KeyError:  # deleted meanwhile
                        continue
                    add(zf, f"conversations/{conv['id']}.json",
                        history.export(conv, "json"))
                    count += 1
                if len(page) < PAGE:
                    break
                offset += PAGE
        return count, path.stat().st_size

    def stream(self, uid: str, folder: Path, path: Path):
        """The zip in chunks; the folder goes (and the export slot frees)
        when it's sent or dropped mid-way. One never started is cleaned up
        by ExportResponse (or the sweep, or deleting the account)."""
        try:
            with open(path, "rb") as f:
                while chunk := f.read(CHUNK):
                    yield chunk
        finally:
            self.finish(uid, folder)


# ---------- HTTP (added by webapp.create_app, after family.register) ----------

def register(app, family, web, error) -> None:
    """GET /api/me/export and POST /api/me/delete (see the module doc).
    With [family] off both are 404. Cross-site requests are refused (403)
    by webapp's guard before they get here."""
    from fastapi import Request
    from fastapi.responses import JSONResponse, StreamingResponse
    from starlette.concurrency import run_in_threadpool

    from assistant import webapp

    class ExportResponse(StreamingResponse):
        """Runs `cleanup` when the response ends, however it ends: a
        generator's `finally` (and a BackgroundTask) never run when the
        client leaves before the body starts (ASGI 2.4: ClientDisconnect)."""

        def __init__(self, content, cleanup: Callable[[], None], **kw):
            super().__init__(content, **kw)
            self.cleanup = cleanup

        async def __call__(self, scope, receive, send):
            try:
                await super().__call__(scope, receive, send)
            finally:
                self.cleanup()

    account = Account(family) if family is not None else None
    app.state.account = account  # tests swap its revoke, clock and tmp_root

    def member(request: Request):
        """(their id, error response): a listed family member's own
        session, else 401 (not signed in) or 403 (the owner)."""
        sid = web.sid(request)
        uid = web.user_of(sid) if sid else webapp.GONE
        if uid == webapp.GONE:
            return None, error("not signed in", 401)
        if account is None:
            return None, error("not found", 404)
        if uid is None:
            return None, error("the owner's account can't be deleted or "
                               "exported here", 403)
        if uid == webapp.GONE or family.get(uid) is None:
            return None, error("not signed in", 401)
        return uid, None

    @app.post("/api/me/delete")
    async def delete_me(request: Request):
        uid, err = member(request)
        if err is not None:
            return err
        try:
            body = await request.json()
        except ValueError:
            body = {}
        given = body.get("confirm") if isinstance(body, dict) else None
        if not confirmed(given, family.get(uid) or {}):
            return error(f"type {CONFIRM_WORD} to confirm", 400)
        sign_out = app.state.family_sign_out  # family.register's
        try:
            removed = await run_in_threadpool(account.delete, uid, sign_out)
        except OSError:
            return error("couldn't delete your account; try again", 500)
        if not removed:
            return error("not signed in", 401)
        response = JSONResponse({"ok": True})
        webapp.clear_session_cookie(response)
        response.headers["Clear-Site-Data"] = '"cookies"'
        return response

    @app.get("/api/me/export")
    async def export_me(request: Request):
        uid, err = member(request)
        if err is not None:
            return err
        try:
            folder, path = await run_in_threadpool(account.build, uid)
        except ExportError as e:
            if e.status == 413:
                log.warning("Account: %s's export is over the cap", uid)
            return error(str(e), e.status)
        return ExportResponse(
            account.stream(uid, folder, path),
            lambda: account.finish(uid, folder), media_type="application/zip",
            headers={"Content-Disposition":
                     'attachment; filename="hey-claude-data.zip"',
                     "Content-Length": str(path.stat().st_size)})
