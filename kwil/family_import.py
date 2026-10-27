"""Moving a family member's conversations from their own PC instance (e.g.
Mom's `config.mom.toml`, `data/mom/history.sqlite`) into their account on
the cloud server.

  POST /api/users/{id}/history/import   {conversations: [...]} -> {imported, skipped}

Owner only (WEB_TOKEN; a family member gets 404, nobody signed in 401).
Each conversation is what `history.export(conv, "json")` gives (also the
"Download my data" zip's files); HistoryStore.import_conversations writes
it with its original id and times into the member's own history, so a
retry skips what is already there. The member must be listed and have
accepted their invite. The write happens under `Family.while_listed`, so
removing them at the same time either waits for it or makes it 404, and
their folder never comes back. A batch that would take their history past
`[family] max_history_mb` gets 413 and nothing of it is kept (prune would
otherwise delete their oldest conversations to make room). Bodies may be
as big as `[webapp] max_upload_mb` (cloud.is_upload). Logs carry ids and
counts only, never titles or text.

The PC side:

  python -m kwil.family_import --from data/mom/history.sqlite \\
      --to https://<cloud host> --user <their id u_..., or name> [--dry-run]

copies the database first (backup.copy_database: a running PC instance is
never disturbed) into a private temp folder, deleted afterwards (if it
can't be, e.g. a file Windows kept locked, it prints the folder's path and
exits 1), reads every conversation through the store's export, and posts them in batches under BATCH_BYTES. It signs in with the cloud's
WEB_TOKEN from the KWIL_CLOUD_TOKEN environment variable, sent only as an
`Authorization: Bearer` header, never in a URL, never printed. It prints
counts only. `--user` is their id or, looked up in GET /api/users, the
name in the invite list. `--dry-run` reads and counts, and sends nothing.
"""

# No `from __future__ import annotations`: FastAPI must see the real
# Request class in the route signature (imported inside register()).
import argparse
import json
import logging
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

TOKEN_ENV = "KWIL_CLOUD_TOKEN"
BATCH_BYTES = 3_500_000   # per request, under the server's max_upload_mb (4)
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
PAGE = 200                # conversations read per history.list call
TRANSPORT = None          # an httpx transport (tests use a MockTransport)


class ImportRefused(Exception):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


# ---------- the cloud side ----------
def import_into(family, uid: str, conversations: list) -> tuple[int, int]:
    """(imported, skipped) for `uid`'s history, or ImportRefused."""
    from kwil.history import HistoryFull

    with family.while_listed(uid) as listed:
        user = family.get(uid) if listed else None
        if user is None:
            raise ImportRefused("not found", 404)
        ctx = family.context(uid)
        if ctx is None:  # listed but hasn't accepted the invite yet
            raise ImportRefused("they haven't accepted their invite yet", 409)
        if ctx.history is None:
            raise ImportRefused("history is off", 409)
        try:
            done, skipped = ctx.history.import_conversations(conversations)
        except HistoryFull:
            raise ImportRefused("that would take their history past "
                                "[family] max_history_mb", 413) from None
        except ValueError:
            raise ImportRefused("a conversation isn't in the export's shape", 400) \
                from None
    log.info("Family: imported %d conversation(s) into %s's history "
             "(%d already there)", len(done), uid, skipped)
    return len(done), skipped


def register(app, family, error) -> None:
    """The owner's import route (see the module doc). 404 with [family] off."""
    from fastapi import Depends, Request
    from starlette.concurrency import run_in_threadpool

    from kwil import webapp

    @app.post("/api/users/{uid}/history/import")
    async def import_history(uid: str, request: Request,
                             session=Depends(webapp.require_owner)):
        if family is None:
            return error("not found", 404)
        try:
            body = await request.json()
        except ValueError:
            return error("bad request", 400)
        conversations = body.get("conversations") if isinstance(body, dict) else None
        if not isinstance(conversations, list):
            return error("bad request", 400)
        try:
            imported, skipped = await run_in_threadpool(
                import_into, family, uid, conversations)
        except ImportRefused as e:
            return error(str(e), e.status)
        return {"imported": imported, "skipped": skipped}


# ---------- the PC side ----------
def server_origin(origin: str) -> str:
    """`origin` as scheme://host[:port] if it is https (or http to this
    machine) and nothing more; ValueError (never quoting it) otherwise."""
    parts = urlsplit((origin or "").strip())
    local = parts.hostname in LOCAL_HOSTS
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        raise ValueError("--to must be an https:// address (http only for localhost)")
    if not parts.hostname or parts.username or parts.password \
            or parts.query or parts.fragment or parts.path not in ("", "/"):
        raise ValueError("--to must be just the server's address, "
                         "like https://example.onrender.com")
    return f"{parts.scheme}://{parts.netloc}"


def find_member(http, origin: str, user: str, headers: dict) -> str:
    """`user` if it is a member id, else the id of the one member with that
    name (GET /api/users); ValueError if there is no such one."""
    from kwil.family import valid_id

    if valid_id(user):
        return user
    r = http.get(f"{origin}/api/users", headers=headers)
    if r.status_code != 200:
        raise ValueError(f"couldn't list the members ({_refusal(r)})")
    wanted = (user or "").strip().casefold()
    found = [u for u in r.json().get("users", [])
             if str(u.get("name", "")).strip().casefold() == wanted]
    if len(found) != 1:
        raise ValueError("--user must be a member's id (u_...) or the name of "
                         "exactly one member")
    if found[0].get("status") != "active":
        raise ValueError("they haven't accepted their invite yet")
    return found[0]["id"]


def read_conversations(path: Path, copy: Path) -> list[dict]:
    """Every conversation in a history database, as history.export's JSON,
    read from a private copy at `copy` (the original is opened read-only,
    once). The caller deletes the copy's folder."""
    from kwil import backup, history

    backup.copy_database(path, copy)
    store = history.HistoryStore(copy)  # no retention or size limit: nothing pruned
    try:
        out, offset = [], 0
        while True:
            page = store.list(limit=PAGE, offset=offset, archived=None)
            for conv in page:
                out.append(json.loads(history.export(store.get(conv["id"]), "json")))
            if len(page) < PAGE:
                return out
            offset += PAGE
    finally:
        store.close()


def remove_copy(folder: Path, out) -> bool:
    """Delete the private copy's folder; False (and its path printed, so
    it can be deleted by hand) if it is still there, e.g. a file Windows
    kept locked. backup.sweep doesn't look in the system temp folder."""
    from kwil import backup

    backup.cleanup(folder)
    if not folder.exists():
        return True
    out(f"Warning: couldn't delete the temporary copy of the history in "
        f"{folder}; delete that folder yourself.")
    return False


def batches(conversations: list[dict], limit: int = BATCH_BYTES):
    """(request bodies, number too big to send alone): each body is
    {"conversations": [...]} of at most `limit` bytes."""
    head, tail = b'{"conversations":[', b"]}"
    bodies, parts, size, too_big = [], [], len(head) + len(tail), 0
    for conv in conversations:
        part = json.dumps(conv, ensure_ascii=False, separators=(",", ":")).encode()
        if len(head) + len(part) + len(tail) > limit:
            too_big += 1
            continue
        if parts and size + 1 + len(part) > limit:
            bodies.append(head + b",".join(parts) + tail)
            parts, size = [], len(head) + len(tail)
        size += len(part) + (1 if parts else 0)
        parts.append(part)
    if parts:
        bodies.append(head + b",".join(parts) + tail)
    return bodies, too_big


def _refusal(response) -> str:
    try:
        message = response.json().get("error")
    except (ValueError, AttributeError):
        message = None
    message = str(message)[:200] if message else ""
    return f"HTTP {response.status_code}" + (f": {message}" if message else "")


def main(argv=None, environ=None, out=print) -> int:
    import os

    parser = argparse.ArgumentParser(
        prog="python -m kwil.family_import",
        description="Copy a family member's conversation history from their "
                    "PC instance into their account on the cloud server")
    parser.add_argument("--from", dest="source", required=True,
                        help="their history database, e.g. data/mom/history.sqlite")
    parser.add_argument("--to", required=True,
                        help="the cloud server, e.g. https://example.onrender.com")
    parser.add_argument("--user", required=True,
                        help="their member id on the cloud (u_...) or their name "
                             "as in the invite list")
    parser.add_argument("--dry-run", action="store_true",
                        help="read and count only; send nothing")
    args = parser.parse_args(argv)
    environ = os.environ if environ is None else environ
    try:
        origin = server_origin(args.to)
    except ValueError as e:
        out(f"Error: {e}")
        return 2
    token = (environ.get(TOKEN_ENV) or "").strip()
    if not token and not args.dry_run:
        out(f"Error: set {TOKEN_ENV} to the cloud server's WEB_TOKEN first")
        return 2
    source = Path(args.source)
    if not source.is_file():
        out("Error: --from isn't a file")
        return 2
    folder = Path(tempfile.mkdtemp(prefix="kwil-import-"))
    try:
        conversations = read_conversations(source, folder / "history.sqlite")
    except Exception as e:  # noqa: BLE001 - a wrong or damaged file
        conversations = None
        failure = type(e).__name__
    removed = remove_copy(folder, out)
    if conversations is None:
        out(f"Error: couldn't read that history database ({failure})")
        return 1
    status = 0 if removed else 1  # the exit code when everything else worked
    bodies, too_big = batches(conversations, BATCH_BYTES)
    size = sum(len(b) for b in bodies) / 1_000_000
    out(f"Read {len(conversations)} conversation(s), {size:.1f} MB; "
        f"{len(bodies)} batch(es) to send.")
    if too_big:
        out(f"Warning: {too_big} conversation(s) are too big to send and are left out.")
    if args.dry_run:
        out("Dry run: nothing was sent.")
        return status
    import httpx

    imported = skipped = 0
    headers = {"Authorization": f"Bearer {token}", "User-Agent": "Kwil-Import/1"}
    with httpx.Client(transport=TRANSPORT, timeout=180, follow_redirects=False) as http:
        try:
            uid = find_member(http, origin, args.user, headers)
        except ValueError as e:
            out(f"Error: {e}")
            return 2
        except httpx.HTTPError as e:
            out(f"Error: couldn't reach the server ({type(e).__name__})")
            return 1
        url = f"{origin}/api/users/{uid}/history/import"
        out(f"Importing into {uid}.")
        for n, body in enumerate(bodies, 1):
            try:
                r = http.post(url, content=body,
                              headers={**headers, "Content-Type": "application/json"})
            except httpx.HTTPError as e:
                out(f"Error: batch {n} of {len(bodies)} failed ({type(e).__name__}); "
                    f"imported {imported} so far. Run it again to continue.")
                return 1
            if r.status_code != 200:
                out(f"Error: the server refused batch {n} of {len(bodies)} "
                    f"({_refusal(r)}); imported {imported} so far.")
                return 1
            result = r.json()
            imported += int(result.get("imported", 0))
            skipped += int(result.get("skipped", 0))
            out(f"Batch {n} of {len(bodies)}: imported {result.get('imported', 0)}, "
                f"skipped {result.get('skipped', 0)}.")
    out(f"Done: imported {imported}, skipped {skipped} (already there).")
    return 1 if too_big else status


if __name__ == "__main__":
    sys.exit(main())
