"""The owner's backup download: GET /api/backup (added to webapp.create_app).

A zip that is safe to keep off-site, made from an allowlist (nothing else
on the disk is ever added):

  history.sqlite            the owner's conversation history (and its
                            failover event log, which lives in the same file)
  users/users.json          the family list as the owner's page shows it
                            (family.public: names, languages, time zones,
                            status, tier); never an invite's hash
  users/<id>/history.sqlite each listed family member's own history
  waitlist.json             the public waitlist, if there is one
  MANIFEST.json             when it was made, the version, each file's size

Never included: Google tokens, encrypted keys (*.enc), signed-in sessions,
.env or any other secret, logs, lecture audio, people already removed.

Each database is copied with sqlite3's online backup into a temp file (a
consistent copy while the app keeps writing; then switched out of WAL mode
so the copy is one self-contained file). The zip is built in a private temp
folder, refused above `[webapp] backup_max_mb`, streamed, and the folder is
deleted when the download ends (or fails); one left by a crash (over an
hour old) is removed by the next backup and at startup
(webapp.build_assistant). One backup at a time. Logged at INFO with its
size only.

The owner's only: not signed in is 401, and a family member gets 404 (as
if the route didn't exist; webapp.owner_of).
"""

# No `from __future__ import annotations` here: FastAPI must see the real
# Request class in the route signature (it is imported inside register()).
import json
import logging
import shutil
import sqlite3
import tempfile
import threading
import time
import zipfile
from pathlib import Path

log = logging.getLogger(__name__)

TEMP_PREFIX = "hey-claude-backup-"
STALE_SECONDS = 3600  # a temp folder this old is from a backup that crashed
WAITLIST_FILE = "waitlist.json"  # in the zip; on disk [webapp] waitlist_file


class BackupError(Exception):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


class Backup:
    """What goes in the owner's backup zip, and making it."""

    def __init__(self, *, history_path=None, family=None, waitlist_path=None,
                 max_bytes: int = 200_000_000, temp_dir=None, clock=time.time):
        self.history_path = Path(history_path) if history_path else None
        self.family = family
        self.waitlist_path = Path(waitlist_path) if waitlist_path else None
        self.max_bytes = int(max_bytes)
        self.temp_dir = Path(temp_dir) if temp_dir else Path(tempfile.gettempdir())
        self.clock = clock
        self._busy = threading.Lock()

    @classmethod
    def from_config(cls, config: dict, store=None, family=None) -> "Backup":
        """`store`: the owner's HistoryStore (None when history is off)."""
        path = getattr(store, "path", None)
        # [webapp] waitlist_file (public_site.py); "" = no waitlist in the backup
        waitlist = config["webapp"].get("waitlist_file", "")
        return cls(history_path=path if path and path != ":memory:" else None,
                   family=family, waitlist_path=waitlist or None,
                   max_bytes=int(float(config["webapp"]["backup_max_mb"]) * 1_000_000))

    # ---------- what goes in ----------
    def _databases(self) -> list[tuple[str, Path]]:
        dbs = []
        if self.history_path is not None and self.history_path.is_file():
            dbs.append(("history.sqlite", self.history_path))
        for uid in self._members():
            path = Path(self.family.dir) / uid / "history.sqlite"
            if path.is_file():
                dbs.append((f"users/{uid}/history.sqlite", path))
        return dbs

    def _members(self) -> dict:
        """The listed people (never one being removed), each as the owner's
        page sees them (family.public: an allowlist, never an invite hash
        or any field added later until it is listed there)."""
        if self.family is None:
            return {}
        from assistant.family import public, valid_id

        with self.family.lock:
            users = dict(self.family.users)
        return {uid: public(u) for uid, u in users.items() if valid_id(uid)}

    @staticmethod
    def _size(path: Path) -> int:
        """A database's bytes on disk, its write-ahead log included."""
        total = 0
        for p in (path, path.with_name(path.name + "-wal")):
            try:
                total += p.stat().st_size
            except OSError:
                pass
        return total

    # ---------- making it ----------
    def sweep(self) -> None:
        """Delete temp folders a crashed backup left behind."""
        now = self.clock()
        for old in self.temp_dir.glob(TEMP_PREFIX + "*"):
            try:
                if old.is_dir() and now - old.stat().st_mtime > STALE_SECONDS:
                    shutil.rmtree(old, ignore_errors=True)
            except OSError:
                pass

    def build(self) -> tuple[Path, Path]:
        """(zip file, its temp folder: delete it with `cleanup` when done).
        BackupError 409 while another backup runs, 413 above the cap."""
        if not self._busy.acquire(blocking=False):
            raise BackupError("a backup is already being made", 409)
        try:
            self.sweep()
            dbs = self._databases()
            planned = sum(self._size(p) for _, p in dbs)
            if planned > self.max_bytes:
                raise self._too_large(planned)
            self.temp_dir.mkdir(parents=True, exist_ok=True)
            folder = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX, dir=self.temp_dir))
            try:
                return self._write(folder, dbs), folder
            except BaseException:
                cleanup(folder)
                raise
        finally:
            self._busy.release()

    def _too_large(self, size: int) -> BackupError:
        log.warning("Backup refused: about %d bytes, above the %d byte cap "
                    "(backup_max_mb)", size, self.max_bytes)
        return BackupError(f"the backup would be larger than "
                           f"{self.max_bytes // 1_000_000} MB (backup_max_mb)", 413)

    def _write(self, folder: Path, dbs) -> Path:
        from assistant import __version__

        out = folder / "backup.zip"
        files = []
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for i, (name, path) in enumerate(dbs):
                copy = folder / f"db-{i}.sqlite"
                copy_database(path, copy)
                zf.write(copy, name)
                files.append({"name": name, "bytes": copy.stat().st_size})
                copy.unlink()
                self._check(out)
            members = self._members() if self.family is not None else None
            if members is not None:
                data = json.dumps({"users": members}, indent=1).encode()
                zf.writestr("users/users.json", data)
                files.append({"name": "users/users.json", "bytes": len(data)})
            if self.waitlist_path is not None and self.waitlist_path.is_file():
                if self.waitlist_path.stat().st_size > self.max_bytes:
                    raise self._too_large(self.waitlist_path.stat().st_size)
                zf.write(self.waitlist_path, WAITLIST_FILE)
                files.append({"name": WAITLIST_FILE,
                              "bytes": self.waitlist_path.stat().st_size})
            zf.writestr("MANIFEST.json", json.dumps(
                {"made": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.clock())),
                 "version": __version__, "files": files,
                 "never_included": "tokens, keys, *.enc files, sessions, .env, "
                                   "logs, removed people"}, indent=1))
        self._check(out)
        return out

    def _check(self, out: Path) -> None:
        size = out.stat().st_size
        if size > self.max_bytes:
            raise self._too_large(size)


def copy_database(src: Path, dst: Path) -> None:
    """A consistent copy of a live SQLite database (read-only on `src`)."""
    source = sqlite3.connect(Path(src).resolve().as_uri() + "?mode=ro", uri=True,
                             timeout=10)
    try:
        target = sqlite3.connect(dst)
        try:
            source.backup(target)
            target.execute("PRAGMA journal_mode = DELETE")  # one self-contained file
        finally:
            target.close()
    finally:
        source.close()


def cleanup(folder: Path) -> None:
    """Delete a backup's temp folder (Windows: a file just closed may be
    locked for a moment, so retry; sweep() catches any left)."""
    for _ in range(20):
        try:
            shutil.rmtree(folder)
            return
        except FileNotFoundError:
            return
        except OSError:
            time.sleep(0.1)


def register(app, backup: Backup, owner_of, error) -> None:
    """GET /api/backup. `owner_of(request)`: the owner's Session, None when
    not signed in; it raises a 404 for a family member's session."""
    from fastapi import Request
    from fastapi.responses import FileResponse
    from starlette.background import BackgroundTask
    from starlette.concurrency import run_in_threadpool

    @app.get("/api/backup")
    async def download_backup(request: Request):
        if owner_of(request) is None:
            return error("not signed in", 401)
        try:
            path, folder = await run_in_threadpool(backup.build)
        except BackupError as e:
            return error(str(e), e.status)
        except (OSError, sqlite3.Error) as e:
            log.error("Backup failed (%s)", type(e).__name__)
            return error("couldn't make the backup; try again", 500)
        size = path.stat().st_size
        log.info("Backup downloaded (%d bytes)", size)
        day = time.strftime("%Y-%m-%d", time.gmtime(backup.clock()))
        return FileResponse(path, media_type="application/zip",
                            filename=f"hey-claude-backup-{day}.zip",
                            headers={"Cache-Control": "no-store"},
                            background=BackgroundTask(cleanup, folder))
