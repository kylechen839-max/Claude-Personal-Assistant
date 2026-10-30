"""Sources for a reply: where its facts came from, shown under it in the
text chat ("Sources: Chat on Tue 28 Oct · PHYS 361 syllabus.pdf", and the
pages a web search cited as small numbered superscripts).

Two kinds:
- **Web.** The web_search server tool's answer carries `citations` on its
  text blocks (web_search_result_location: url, title, cited_text). Each
  cited page is a numbered source; the reply gets a mark after the text
  block that cited it.
- **Kwil's own data.** Results of read_conversation, the project file and
  the course file tools are sent to Claude as `search_result` content
  blocks (search_results below; GA in the Messages API, no beta header),
  so the API cites them the same way (search_result_location: source,
  title, cited_text). Their `source` is a kwil: id this module made for
  the turn (`Sources.offer`), so a citation can only name something a tool
  really returned. A read tool that ran but wasn't cited still lists its
  conversation or file (the model may have paraphrased it); the system
  prompt asks Claude to say in words where an answer came from.

Everything here is untrusted display text: titles and cited_text come
from web pages, files and saved chats. It is bounded (MAX_SOURCES, MAX_TITLE,
MAX_URL, MAX_CITED, MAX_MARKS) and only http(s) URLs are kept (safe_url);
the page draws it with textContent (web/sources.js). Spoken replies never
carry any of it: sources are never part of the reply text.

Nothing of it is logged above DEBUG: INFO says counts only.
"""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

MAX_SOURCES = 20
MAX_TITLE = 200
MAX_URL = 2000
MAX_CITED = 300
MAX_MARKS = 50        # superscript positions per reply
MAX_MARK_REFS = 5     # source numbers per position
MAX_STORED_BYTES = 12_000  # a message's citations as JSON (history events are 64 KB)
KINDS = ("web", "chat", "project_file", "course_file")
# the tools whose results go to Claude as search_result blocks
CITABLE_TOOLS = ("read_conversation", "search_project_files", "read_project_file",
                 "search_course_files", "read_course_file")
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _text(value, limit: int) -> str:
    """One line of display text, at most `limit` characters."""
    text = " ".join(_CONTROL.sub(" ", str(value or "")).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def utf16_len(text: str) -> int:
    """`text`'s length in UTF-16 code units (JavaScript's `length`): the
    unit marks are counted in, so the page puts them where they belong."""
    return len(text.encode("utf-16-le")) // 2


def safe_url(url) -> str | None:
    """`url` if it is an http(s) address with a host, no spaces or control
    characters and at most MAX_URL characters; None otherwise (javascript:,
    data:, a relative path...)."""
    if not isinstance(url, str) or len(url) > MAX_URL or url != url.strip() \
            or _CONTROL.search(url) or " " in url:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    return url


def clean(items) -> list[dict]:
    """Stored or imported sources, checked and bounded: the only shape the
    page and history ever get. Unknown kinds and unsafe URLs are dropped."""
    out = []
    for s in items if isinstance(items, list) else []:
        if not isinstance(s, dict) or s.get("kind") not in KINDS:
            continue
        kind = s["kind"]
        item = {"kind": kind, "title": _text(s.get("title"), MAX_TITLE)}
        if kind == "web":
            url = safe_url(s.get("url"))
            if url is None:
                continue
            item["url"] = url
            item["title"] = item["title"] or _text(urlsplit(url).hostname, MAX_TITLE)
            if s.get("cited_text"):
                item["cited_text"] = _text(s["cited_text"], MAX_CITED)
        else:
            ref = s.get("ref")
            if isinstance(ref, int) and not isinstance(ref, bool):
                ref = str(ref)
            if not isinstance(ref, str) or not _ID.match(ref):
                continue
            item["ref"] = ref
            if kind == "project_file":
                project = s.get("project")
                if isinstance(project, str) and _ID.match(project):
                    item["project"] = project
            if not item["title"]:
                continue
        out.append(item)
        if len(out) >= MAX_SOURCES:
            break
    return out


def clean_marks(marks, sources: int) -> list[list]:
    """[[character offset, [source numbers]]], bounded, numbers 1..sources."""
    out = []
    for m in marks if isinstance(marks, list) else []:
        if not (isinstance(m, list) and len(m) == 2 and isinstance(m[0], int)
                and not isinstance(m[0], bool) and m[0] >= 0 and isinstance(m[1], list)):
            continue
        refs = [n for n in m[1] if isinstance(n, int) and not isinstance(n, bool)
                and 1 <= n <= sources][:MAX_MARK_REFS]
        if refs:
            out.append([m[0], refs])
        if len(out) >= MAX_MARKS:
            break
    return out


def stored(cites) -> dict | None:
    """A message's citations as history keeps them ({"sources", "marks"}),
    checked; None when there are none."""
    if not isinstance(cites, dict):
        return None
    sources = clean(cites.get("sources"))
    if not sources:
        return None
    # a history event is at most 64 KB of JSON (failover.sync) with the
    # message's own text: cited text goes first, then the last sources
    if _size(sources) > MAX_STORED_BYTES:
        sources = [{k: v for k, v in s.items() if k != "cited_text"} for s in sources]
    while len(sources) > 1 and _size(sources) > MAX_STORED_BYTES:
        sources.pop()
    if _size(sources) > MAX_STORED_BYTES:
        return None
    marks = clean_marks(cites.get("marks"), len(sources))
    return {"sources": sources, **({"marks": marks} if marks else {})}


def _size(value) -> int:
    return len(json.dumps(value))


def short_date(ts: float, tz=None, language: str = "en") -> str:
    """"Tue 28 Oct" (Chinese: "10月28日")."""
    from kwil.history import local_dt

    dt = local_dt(ts, tz)
    return f"{dt.month}月{dt.day}日" if language == "zh" else f"{dt:%a} {dt.day} {dt:%b}"


class Sources:
    """One turn's sources (ClaudeChat keeps one per turn). Not thread-safe:
    a turn runs on one thread."""

    def __init__(self, language: str = "en", tz=None, project: str | None = None):
        self.language = language
        self.tz = tz
        self.project = project  # the conversation's project (its files' links)
        self.items: list[dict] = []
        self.marks: list[list] = []
        self._offered: dict[str, dict] = {}  # kwil: source id -> source
        self._read: list[str] = []           # ids a read tool returned

    # ---------- own data: tool results -> search_result blocks ----------
    def _own(self, kind: str, ref: str, title: str, **extra) -> str:
        sid = f"kwil:{kind}/{ref}"
        if len(self._offered) < 4 * MAX_SOURCES or sid in self._offered:
            self._offered[sid] = {"kind": kind, "ref": ref,
                                  "title": _text(title, MAX_TITLE), **extra}
        return sid

    def offer(self, tool: str, content: str) -> list[dict] | None:
        """The search_result blocks to send for `tool`'s result `content`
        (its JSON text), or None to send the text as it is (an error, no
        hits, another tool, or a shape it doesn't know)."""
        if tool not in CITABLE_TOOLS:
            return None
        try:
            result = json.loads(content)
        except (TypeError, ValueError):
            return None
        if not isinstance(result, dict):
            return None
        try:
            blocks = getattr(self, "_" + tool)(result)
        except (KeyError, TypeError, ValueError, AttributeError):
            log.warning("Sources: an unexpected %s result; sent as text", tool)
            return None
        blocks = [b for b in blocks or [] if b["content"]]
        return blocks or None

    @staticmethod
    def _block(source: str, title: str, texts) -> dict:
        content = [{"type": "text", "text": t} for t in texts if t and t.strip()]
        return {"type": "search_result", "source": source,
                "title": _text(title, MAX_TITLE) or "(untitled)",
                "content": content, "citations": {"enabled": True}}

    def _read_conversation(self, r: dict) -> list[dict]:
        if "messages" not in r or not r.get("id"):
            return []
        from kwil.history import valid_id

        if not valid_id(r["id"]):
            return []
        label = self._chat_label(r)
        sid = self._own("chat", r["id"], label)
        self._read.append(sid)
        header = (f"Saved chat '{r.get('title') or '(untitled)'}' from "
                  f"{r.get('date') or r.get('when') or 'an earlier day'}. "
                  f"{r.get('note', '')} {r.get('message', '')}").strip()
        if r.get("summary"):
            header += f" Summary: {r['summary']}"
        texts = [header] + [f"{'Kyle' if m.get('who') == 'Kyle' else 'You'} said"
                            f"{' (match)' if m.get('match') else ''}: {m.get('said', '')}"
                            for m in r["messages"] if isinstance(m, dict)]
        return [self._block(sid, f"{label}: {r.get('title') or '(untitled)'}", texts)]

    def _chat_label(self, r: dict) -> str:
        ts = r.get("updated_at")
        if isinstance(ts, (int, float)):
            day = short_date(ts, self.tz, self.language)
        else:
            day = str(r.get("date") or "")
        return (f"{day}的聊天" if self.language == "zh" else f"Chat on {day}").strip()

    def _search_project_files(self, r: dict) -> list[dict]:
        out = []
        for h in r.get("results") or []:
            sid = self._own("project_file", str(h["file_id"]), h["name"],
                            **({"project": self.project} if self.project else {}))
            out.append(self._block(sid, h["name"], [
                f"{r.get('note', '')} (file_id {h['file_id']}, offset {h.get('offset', 0)})",
                h.get("snippet", "")]))
        return out

    def _read_project_file(self, r: dict) -> list[dict]:
        if "text" not in r or not r.get("file_id"):
            return []
        sid = self._own("project_file", str(r["file_id"]), r.get("name") or "file",
                        **({"project": self.project} if self.project else {}))
        self._read.append(sid)
        tail = (f" More from next_offset {r['next_offset']}." if r.get("next_offset")
                else "")
        return [self._block(sid, r.get("name") or "file", [
            f"{r.get('note', '')} (file_id {r['file_id']}, characters "
            f"{r.get('offset', 0)}-{r.get('offset', 0) + len(r['text'])} of "
            f"{r.get('chars', len(r['text']))}).{tail} {r.get('message', '')}".strip(),
            r["text"]])]

    def _course_title(self, h: dict) -> str:
        course, title = str(h.get("course") or ""), str(h.get("title") or "")
        return f"{course} {title}".strip() if course and course not in title else title

    def _search_course_files(self, r: dict) -> list[dict]:
        out = []
        for h in r.get("results") or []:
            title = self._course_title(h) or "course document"
            sid = self._own("course_file", str(int(h["doc_id"])), title)
            out.append(self._block(sid, title, [
                f"{r.get('note', '')} (doc_id {int(h['doc_id'])}, {h.get('kind', '')})",
                h.get("snippet", "")]))
        return out

    def _read_course_file(self, r: dict) -> list[dict]:
        if "text" not in r or r.get("doc_id") is None:
            return []
        title = self._course_title(r) or "course document"
        sid = self._own("course_file", str(int(r["doc_id"])), title)
        self._read.append(sid)
        tail = (f" More from next_offset {r['next_offset']}." if r.get("next_offset")
                else "")
        return [self._block(sid, title, [
            f"{r.get('note', '')} (doc_id {int(r['doc_id'])}, from character "
            f"{r.get('offset', 0)}).{tail}".strip(), r["text"]])]

    # ---------- what the reply cites ----------
    def _number(self, item: dict) -> int | None:
        key = (item["kind"], item.get("url") or item.get("ref"))
        for n, s in enumerate(self.items, 1):
            if (s["kind"], s.get("url") or s.get("ref")) == key:
                return n
        if len(self.items) >= MAX_SOURCES:
            return None
        self.items.append(item)
        return len(self.items)

    def cite(self, citation) -> int | None:
        """A citation from a response's text block (an SDK object or a
        dict): its source's number, or None if it isn't one to show."""
        c = citation if isinstance(citation, dict) else (
            citation.model_dump() if hasattr(citation, "model_dump") else {})
        kind = c.get("type")
        if kind == "web_search_result_location":
            item = clean([{"kind": "web", "url": c.get("url"), "title": c.get("title"),
                           "cited_text": c.get("cited_text")}])
            return self._number(item[0]) if item else None
        if kind == "search_result_location":
            offered = self._offered.get(str(c.get("source") or ""))
            if offered is None:  # not one this turn's tools returned
                return None
            item = clean([offered])
            return self._number(item[0]) if item else None
        return None

    def reply(self, blocks) -> str:
        """The reply text of a final response's `blocks` (text blocks
        joined, then stripped, as ClaudeChat returns it), noting a mark
        after each text block that cites something. Marks count UTF-16
        code units, as the page's strings do (an emoji is two), and a mark
        always falls between whole characters."""
        text, marks = "", []
        for b in blocks:
            if getattr(b, "type", None) != "text":
                continue
            text += b.text
            refs = []
            for c in getattr(b, "citations", None) or []:
                n = self.cite(c)
                if n is not None and n not in refs:
                    refs.append(n)
            if refs:
                marks.append([utf16_len(text.rstrip()), refs[:MAX_MARK_REFS]])
        lead = utf16_len(text) - utf16_len(text.lstrip())
        out = text.strip()
        end = utf16_len(out)
        for at, refs in marks:
            at = max(0, min(end, at - lead))
            if self.marks and self.marks[-1][0] == at:
                self.marks[-1][1] = (self.marks[-1][1] + [
                    n for n in refs if n not in self.marks[-1][1]])[:MAX_MARK_REFS]
            elif len(self.marks) < MAX_MARKS:
                self.marks.append([at, refs])
        return out

    def finish(self) -> dict | None:
        """The turn's citations ({"sources", "marks"}) or None. A read
        tool's conversation or file is listed even when not cited."""
        for sid in self._read:
            item = clean([self._offered[sid]]) if sid in self._offered else []
            if item:
                self._number(item[0])
        result = stored({"sources": self.items, "marks": self.marks})
        if result:
            log.info("Sources: %d (%s)", len(result["sources"]), ", ".join(sorted(
                {s["kind"] for s in result["sources"]})))
        return result


# The system prompt's line about saying where an answer came from.
CITE_PROMPT = (
    "When you answer from a saved past chat, a project or course file or "
    "something you remember about Kyle, say briefly where it came from "
    "(\"from your chat on Tuesday\", \"from the PHYS 361 syllabus\"). Never "
    "read out web addresses; the app lists sources under your reply.")


def domain(url: str) -> str:
    """"physics.example.edu" ("www." dropped)."""
    host = (urlsplit(url).hostname or "") if isinstance(url, str) else ""
    return host[4:] if host.startswith("www.") else host


def export_line(source: dict) -> str:
    """One source in an exported transcript: "Title (https://...)" for a
    web page, else its label ("Chat on Tue 28 Oct")."""
    if source.get("kind") == "web":
        return f"{source.get('title') or domain(source.get('url', ''))} ({source.get('url')})"
    return str(source.get("title") or "")
