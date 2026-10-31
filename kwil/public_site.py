"""The public site: a landing page, privacy and terms, the key guide, and
the waitlist.

  GET    /welcome                    what Kwil is, the waitlist form
  GET    /privacy, /terms            the beta's privacy notice and terms
  GET    /help/anthropic-key         how to get an Anthropic API key, and
                                     what it costs (credit.HELP_PATH: linked
                                     from beta invites, the "starter credit
                                     is used up" sentence and Settings)
  GET    /site.css                   their stylesheet (no inline CSS or JS:
                                     the app's strict CSP holds here too)
  POST   /api/waitlist               {email, note?} as a form or JSON (public)
  GET    /api/waitlist               the entries (owner only)
  DELETE /api/waitlist/{id}          remove one (owner only; the owner page
  POST   /api/waitlist/{id}/remove   posts the form version)
  GET    /waitlist                   the owner's list (web/waitlist.html), with
                                     a remove button per entry

All of it is off unless `[webapp] public_site` is true (it is turned on at
the public beta's launch): then these routes don't exist (404) and the
sign-in page has no link to /welcome.

The form works without JavaScript: a plain POST, then a 303 back to
/welcome?waitlist=<state>. A JSON post gets JSON back. Entries are kept in
`[webapp] waitlist_file` (a private file written atomically): addresses
lower-cased and de-duplicated, the note cut to MAX_NOTE characters, at most
MAX_ENTRIES of them. Each address may post PER_HOUR times an hour (the
client address CloudGuard worked out, from the scope; off the cloud the web
app's own client_ip); a filled
honeypot field is dropped without a word. Entries are deleted by an opaque id,
so an address never sits in a URL (or the access log). The log gets counts
only, never an address or a note.

Behind CloudGuard (cloud.py) the pages and POST /api/waitlist are public,
the POST in a body pool of its own; the owner routes need the owner's sign-in
and answer 404 to a family member, like every other owner-only route.
"""

# No `from __future__ import annotations`: FastAPI reads the routes'
# annotations (Request) at runtime.
import html
import json
import logging
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

STATIC = Path(__file__).resolve().parent / "web"
PAGES = {"/welcome": "landing.html", "/privacy": "privacy.html",
         "/terms": "terms.html", "/help/anthropic-key": "help-anthropic-key.html"}
MAX_ENTRIES = 2000
MAX_NOTE = 300
PER_HOUR = 5               # waitlist posts per address an hour
CLIENT_KEY = "client_key"  # scope["state"] key CloudGuard sets (cloud.py)
HONEYPOT = "website"       # hidden from people by site.css; bots fill it in
# The landing page's notice after a post (?waitlist=<state>). A repeat of an
# address already on the list gets "thanks" too: the page never tells
# whether someone else's address is on it.
NOTICES = {
    "thanks": "Thanks! You're on the list. If a spot opens up, you'll get an "
              "invite by email.",
    "invalid": "That doesn't look like an email address. Please check it and "
               "try again.",
    "busy": "Too many tries from this network. Please try again in an hour.",
    "full": "The waitlist is full for now. Please check back later.",
    "closed": "The waitlist is closed right now.",
    "error": "Something went wrong on our side. Please try again later.",
}
STATUS = {"thanks": 200, "invalid": 400, "busy": 429, "full": 503,
          "closed": 503, "error": 503}
# The sign-in page's link here (web/index.html); taken out when the site is off.
LOGIN_LINK = '<p class="login-more"><a href="/welcome">What is Kwil?</a></p>'
DRAFT_NOTE = ('<p class="draft" role="note"><strong>Draft: Kyle to review.</strong> '
              'This wording is not final yet.</p>')

_LOCAL = re.compile(r"[a-z0-9!#$%&'*+/=?^_`{|}~.-]{1,64}")
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def normalize_email(value) -> str | None:
    """The address lower-cased and trimmed, or None if it isn't one (plain
    ASCII user@domain.tld; at most 254 characters)."""
    if not isinstance(value, str):
        return None
    email = value.strip().lower()
    if len(email) > 254 or email.count("@") != 1:
        return None
    local, domain = email.split("@")
    if (not _LOCAL.fullmatch(local) or local.startswith(".")
            or local.endswith(".") or ".." in local):
        return None
    labels = domain.split(".")
    if len(labels) < 2 or not all(_LABEL.fullmatch(p) for p in labels) \
            or labels[-1].isdigit():
        return None
    return email


def clean_note(value) -> str:
    """The "what would you use it for" answer: one line, no control
    characters, at most MAX_NOTE characters."""
    if not isinstance(value, str):
        return ""
    text = " ".join("".join(c if c.isprintable() else " " for c in value).split())
    return text[:MAX_NOTE]


class WaitlistError(Exception):
    """The waitlist file can't be read (it is never overwritten then)."""


class Waitlist:
    """The entries in one private JSON file, loaded and saved per change
    (at most MAX_ENTRIES small entries)."""

    def __init__(self, path: str | Path | None, max_entries: int = MAX_ENTRIES):
        self.path = Path(path) if path else None
        self.max_entries = max_entries
        self.lock = threading.Lock()

    def _load(self) -> list[dict]:
        if self.path is None or not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            entries = data["entries"]
            if not isinstance(entries, list):
                raise TypeError
            return [e for e in entries if isinstance(e, dict)]
        except (OSError, ValueError, KeyError, TypeError) as e:
            log.error("Waitlist: can't read %s (%s)", self.path, type(e).__name__)
            raise WaitlistError from None

    def _save(self, entries: list[dict]) -> None:
        from kwil.tools.google_auth import write_private

        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_private(self.path, json.dumps({"version": 1, "entries": entries},
                                            ensure_ascii=False, indent=1))

    def add(self, email: str, note: str = "") -> str:
        """"thanks" (added, or already on the list), "full" or "closed"."""
        if self.path is None:
            return "closed"
        with self.lock:
            entries = self._load()
            # "full" for everyone once at the cap, already listed or not: the
            # answer never tells whether an address is on the list
            if len(entries) >= self.max_entries:
                log.warning("Waitlist: full (%d entries)", len(entries))
                return "full"
            if any(e.get("email") == email for e in entries):
                return "thanks"  # kept as first given
            entries.append({"id": secrets.token_hex(8), "email": email,
                            "note": note,
                            "joined": datetime.now(timezone.utc)
                            .isoformat(timespec="seconds")})
            self._save(entries)
        log.info("Waitlist: %d entries", len(entries))
        return "thanks"

    def entries(self) -> list[dict]:
        with self.lock:
            return self._load()

    def remove(self, entry_id: str) -> bool:
        with self.lock:
            entries = self._load()
            kept = [e for e in entries if e.get("id") != entry_id]
            if len(kept) == len(entries):
                return False
            self._save(kept)
        log.info("Waitlist: an entry removed, %d left", len(kept))
        return True


class PublicSite:
    """The pages' settings ([webapp] support_email, legal_draft,
    waitlist_file), the waitlist and its per-address limit."""

    def __init__(self, config: dict, clock=time.monotonic):
        from kwil.cloud import RateLimiter

        web = config["webapp"]
        self.support_email = normalize_email(web.get("support_email") or "") or ""
        self.draft = bool(web.get("legal_draft", True))
        self.waitlist = Waitlist(web.get("waitlist_file") or None)
        self.limiter = RateLimiter(PER_HOUR, clock=clock, window=3600)

    def page(self, path: str, notice: str = "") -> str:
        text = (STATIC / PAGES[path]).read_text(encoding="utf-8")
        if self.support_email:
            address = html.escape(self.support_email)
            contact = f'<a href="mailto:{address}">{address}</a>'
        else:
            contact = "the person who runs this server"
        message = NOTICES.get(notice)
        status = (f'<p class="notice notice-{notice}" role="status">'
                  f"{html.escape(message)}</p>" if message else "")
        return (text.replace("{{SUPPORT_EMAIL}}", contact)
                .replace("{{DRAFT}}", DRAFT_NOTE if self.draft else "")
                .replace("{{WAITLIST_STATUS}}", status))

    def join(self, key: str, fields: dict) -> str:
        """One waitlist post from client `key` (cloud.client_key form): its
        state (a NOTICES key)."""
        if not self.limiter.allow(key):
            log.warning("Waitlist: one address over its limit")
            return "busy"
        if fields.get(HONEYPOT):
            log.info("Waitlist: a filled-in honeypot, dropped")
            return "thanks"  # the bot learns nothing
        email = normalize_email(fields.get("email"))
        if email is None:
            log.info("Waitlist: not an email address")
            return "invalid"
        try:
            return self.waitlist.add(email, clean_note(fields.get("note")))
        except (WaitlistError, OSError) as e:
            # the error's type only, never its locals or the entry
            log.error("Waitlist: saving failed (%s)", type(e).__name__)
            return "error"


def from_config(config: dict) -> "PublicSite | None":
    """The site, or None while `[webapp] public_site` is off."""
    return PublicSite(config) if config["webapp"]["public_site"] else None


def client_key_of(request, web) -> str:
    """Who a post counts against: the key CloudGuard computed for this
    request (its client_ip_header or the rightmost untrusted
    X-Forwarded-For hop, IPv6 per /64), else the web app's own address."""
    from kwil.cloud import client_key

    key = (request.scope.get("state") or {}).get(CLIENT_KEY)
    return key if isinstance(key, str) and key else client_key(web.client_ip(request))


def owner_page(entries: list[dict]) -> str:
    """The owner's list (GET /waitlist): every value escaped, the notes are
    strangers' text."""
    rows = []
    for e in entries:
        eid = html.escape(str(e.get("id", "")), quote=True)
        rows.append(
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>"
            '<form method="post" action="/api/waitlist/{}/remove">'
            '<button type="submit">Remove</button></form></td></tr>'.format(
                html.escape(str(e.get("email", ""))),
                html.escape(str(e.get("note", ""))),
                html.escape(str(e.get("joined", ""))[:10]), eid))
    body = ("<table><thead><tr><th>Email</th><th>Use</th><th>Joined</th>"
            "<th></th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
            if rows else "<p>Nobody is on the waitlist yet.</p>")
    return (STATIC / "waitlist.html").read_text(encoding="utf-8").replace(
        "{{COUNT}}", str(len(entries))).replace("{{ENTRIES}}", body)


def register(app, site: PublicSite, web, error) -> None:
    """The routes in the module docstring."""
    from fastapi import Depends, Request
    from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
    from starlette.concurrency import run_in_threadpool

    from kwil import webapp

    def page_route(path: str):
        def serve_page(waitlist: str = ""):
            return Response(site.page(path, waitlist if path == "/welcome" else ""),
                            media_type="text/html; charset=utf-8",
                            headers={"Cache-Control": "no-cache"})
        serve_page.__name__ = "public_" + path.strip("/").replace("/", "_").replace("-", "_")
        return serve_page

    for path in PAGES:
        app.add_api_route(path, page_route(path), methods=["GET"])

    @app.get("/site.css")
    def site_css():
        return FileResponse(STATIC / "site.css", media_type="text/css")

    @app.post("/api/waitlist")
    async def join(request: Request):
        ctype = request.headers.get("content-type", "")
        as_json = ctype.startswith("application/json")
        fields: dict = {}
        try:
            if as_json:
                body = await request.json()
                fields = body if isinstance(body, dict) else {}
            elif ctype.startswith(("application/x-www-form-urlencoded",
                                   "multipart/form-data")):
                form = await request.form(max_files=0, max_fields=5)
                fields = {k: v for k, v in form.items() if isinstance(v, str)}
            else:
                return error("send a form or JSON", 415)
        except Exception:  # noqa: BLE001 - any malformed body
            fields = {}
        state = await run_in_threadpool(site.join, client_key_of(request, web), fields)
        if as_json:
            if state == "thanks":
                return {"ok": True}
            return JSONResponse({"error": NOTICES[state]}, STATUS[state])
        return RedirectResponse(f"/welcome?waitlist={state}#waitlist", 303)

    @app.get("/api/waitlist")
    def list_entries(session=Depends(webapp.require_owner)):
        try:
            entries = site.waitlist.entries()
        except WaitlistError:
            return error("couldn't read the waitlist", 500)
        return {"entries": entries, "count": len(entries),
                "max": site.waitlist.max_entries}

    def remove(entry_id: str) -> bool | None:
        try:
            return site.waitlist.remove(entry_id)
        except (WaitlistError, OSError):
            return None

    @app.delete("/api/waitlist/{entry_id}")
    async def delete_entry(entry_id: str, session=Depends(webapp.require_owner)):
        removed = await run_in_threadpool(remove, entry_id)
        if removed is None:
            return error("couldn't change the waitlist; try again", 500)
        if not removed:
            return error("not found", 404)
        return {"ok": True}

    @app.post("/api/waitlist/{entry_id}/remove")
    async def remove_entry(entry_id: str, session=Depends(webapp.require_owner)):
        removed = await run_in_threadpool(remove, entry_id)
        if removed is None:
            return error("couldn't change the waitlist; try again", 500)
        return RedirectResponse("/waitlist", 303)

    @app.get("/waitlist")
    def owner_list(session=Depends(webapp.require_owner)):
        try:
            entries = site.waitlist.entries()
        except WaitlistError:
            return error("couldn't read the waitlist", 500)
        return Response(owner_page(entries), media_type="text/html; charset=utf-8",
                        headers={"Cache-Control": "no-store"})
