"""The text of a course file (coursefiles.py): PDF, Word (.docx),
PowerPoint (.pptx), HTML and plain text. Nothing else (images, video,
audio, spreadsheets, archives) is read.

The type is sniffed from the bytes, never taken from Canvas's
content-type or the file name (both are whatever the uploader said):

- PDF ("%PDF-" near the start): pypdf, page by page, at most MAX_PDF_PAGES
  pages, the time checked between pages. pypdf is pure Python (one wheel
  for Windows, Linux and the Pi); without it a PDF is "no_pdf_reader" and
  everything else still works.
- DOCX / PPTX (a zip holding word/document.xml or ppt/slides/): the
  standard library's zipfile and xml.etree, no lxml. The zip's declared
  uncompressed sizes are checked first (MAX_ZIP_BYTES, MAX_ZIP_MEMBERS),
  and each member is read with a cap too (a zip can lie about its sizes):
  a zip bomb is refused, not inflated. An XML part with a DTD is refused
  (OOXML never has one; entity expansion is how XML bombs work).
- HTML: tools.gmail.html_to_text (at most MAX_HTML_CHARS of markup).
- Plain text (UTF-8, or Latin-1 when that fails), when it has no NUL bytes.

extract() returns at most `max_chars` characters. Errors are ExtractError
with a short code ("unsupported", "too_large", "timed_out", "encrypted",
"unreadable", "no_pdf_reader"), never the file's text. Nothing is logged
but codes and counts.

pypdf can't be interrupted inside one page: a hostile PDF can hold one
call for as long as that page takes. The page cap and the caller's file
size cap bound it; a subprocess would be the next step if that matters.
"""

from __future__ import annotations

import io
import logging
import re
import time
import zipfile
from typing import Callable
from xml.etree import ElementTree

log = logging.getLogger(__name__)

MAX_PDF_PAGES = 300          # pages of one PDF read
MAX_CHARS = 1_000_000        # characters of text kept from one file
MAX_ZIP_BYTES = 60_000_000   # a .docx/.pptx's declared uncompressed size
MAX_ZIP_MEMBERS = 5000       # ... and its number of parts
MAX_XML_BYTES = 20_000_000   # one XML part read
MAX_SLIDES = 500             # slides of one .pptx read
MAX_HTML_CHARS = 2_000_000   # markup given to html_to_text (its regexes)
KINDS = ("pdf", "docx", "pptx", "html", "text")
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_SLIDE = re.compile(r"^ppt/slides/slide(\d{1,5})\.xml$")
_HTML = re.compile(rb"<\s*(!doctype\s+html|html|head|body|p|div|h[1-6]|table|ul|span)\b",
                   re.I)


class ExtractError(ValueError):
    """A file whose text can't be read; the message is a short code."""


def _check_time(deadline: float | None, clock: Callable[[], float]) -> None:
    if deadline is not None and clock() > deadline:
        raise ExtractError("timed_out")


def sniff(data: bytes) -> str | None:
    """"pdf", "docx", "pptx", "html", "text", or None (anything else),
    from the bytes alone."""
    head = data[:1024]
    if b"%PDF-" in head:
        return "pdf"
    if data[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = set(zf.namelist()[:MAX_ZIP_MEMBERS + 1])
        except (zipfile.BadZipFile, OSError, ValueError, RuntimeError):
            return None
        if "word/document.xml" in names:
            return "docx"
        if any(_SLIDE.match(n) for n in names):
            return "pptx"
        return None  # a spreadsheet, an archive, ...
    sample = data[:4096]
    if b"\x00" in sample:
        return None  # binary: an image, audio, video, ...
    if _HTML.search(sample):
        return "html"
    text = _decode(sample)
    if text and sum(c.isprintable() or c in "\r\n\t" for c in text) < 0.9 * len(text):
        return None  # binary without a NUL in its first bytes
    return "text"


def _decode(data: bytes) -> str:
    if data[:3] == b"\xef\xbb\xbf":
        data = data[3:]
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def tidy(text: str, max_chars: int) -> str:
    text = text[:max_chars * 2].replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(c for c in text if c.isprintable() or c in "\n\t")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()[:max_chars]


# ---------------------------------------------------------------- PDF
def _pdf(data: bytes, max_pages: int, max_chars: int, deadline, clock) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ExtractError("no_pdf_reader") from None
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:  # many "protected" PDFs open with an empty password
                if not reader.decrypt(""):
                    raise ExtractError("encrypted")
            except ExtractError:
                raise
            except Exception:  # noqa: BLE001 - e.g. an unsupported cipher
                raise ExtractError("encrypted") from None
        parts, total = [], 0
        for number, page in enumerate(reader.pages):
            if number >= max_pages:
                log.info("Text: PDF longer than %d pages; the rest skipped", max_pages)
                break
            _check_time(deadline, clock)
            text = page.extract_text() or ""
            parts.append(text)
            total += len(text)
            if total >= max_chars:
                break
        return "\n\n".join(parts)
    except ExtractError:
        raise
    except Exception as e:  # noqa: BLE001 - pypdf raises many kinds on bad files
        log.debug("Text: PDF unreadable (%s)", type(e).__name__)
        raise ExtractError("unreadable") from None


# ---------------------------------------------------------------- DOCX / PPTX
def _zip(data: bytes) -> zipfile.ZipFile:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError, ValueError, RuntimeError):
        raise ExtractError("unreadable") from None
    infos = zf.infolist()
    if len(infos) > MAX_ZIP_MEMBERS or sum(i.file_size for i in infos) > MAX_ZIP_BYTES:
        zf.close()
        raise ExtractError("too_large")  # a zip bomb, or just too big
    return zf


def _xml(zf: zipfile.ZipFile, name: str):
    """One XML part, parsed; read with a cap whatever the zip declares."""
    try:
        with zf.open(name) as f:
            raw = f.read(MAX_XML_BYTES + 1)
    except KeyError:
        return None
    except (zipfile.BadZipFile, OSError, ValueError, RuntimeError, EOFError):
        raise ExtractError("unreadable") from None
    if len(raw) > MAX_XML_BYTES:
        raise ExtractError("too_large")
    if b"<!DOCTYPE" in raw[:4096].upper() or b"<!ENTITY" in raw.upper():
        raise ExtractError("unreadable")  # never in OOXML: an XML bomb
    try:
        return ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        raise ExtractError("unreadable") from None


def _paragraphs(root, para: str, text: str, tab: str | None, br: str | None) -> list[str]:
    out = []
    for p in root.iter(para):
        bits = []
        for node in p.iter():
            if node.tag == text and node.text:
                bits.append(node.text)
            elif node.tag in (tab, br):
                bits.append("\t" if node.tag == tab else "\n")
        line = "".join(bits).strip()
        if line:
            out.append(line)
    return out


def _docx(data: bytes, deadline, clock) -> str:
    with _zip(data) as zf:
        _check_time(deadline, clock)
        root = _xml(zf, "word/document.xml")
        if root is None:
            raise ExtractError("unreadable")
        return "\n".join(_paragraphs(root, W + "p", W + "t", W + "tab", W + "br"))


def _pptx(data: bytes, deadline, clock) -> str:
    with _zip(data) as zf:
        slides = sorted((int(m.group(1)), n) for n in zf.namelist()
                        if (m := _SLIDE.match(n)))
        out = []
        for number, name in slides[:MAX_SLIDES]:
            _check_time(deadline, clock)
            root = _xml(zf, name)
            lines = _paragraphs(root, A + "p", A + "t", None, A + "br") if root is not None \
                else []
            if lines:
                out.append(f"Slide {number}\n" + "\n".join(lines))
        return "\n\n".join(out)


# ---------------------------------------------------------------- all
def extract(data: bytes, deadline: float | None = None,
            clock: Callable[[], float] = time.monotonic,
            max_pages: int = MAX_PDF_PAGES, max_chars: int = MAX_CHARS) -> tuple[str, str]:
    """(kind, text) of a file's bytes; ExtractError when it can't be read
    (see the module docstring)."""
    kind = sniff(data)
    if kind is None:
        raise ExtractError("unsupported")
    _check_time(deadline, clock)
    if kind == "pdf":
        text = _pdf(data, max_pages, max_chars, deadline, clock)
    elif kind == "docx":
        text = _docx(data, deadline, clock)
    elif kind == "pptx":
        text = _pptx(data, deadline, clock)
    elif kind == "html":
        text = html_text(_decode(data))
    else:
        text = _decode(data)
    return kind, tidy(text, max_chars)


def html_text(markup: str) -> str:
    """Readable text of Canvas HTML (a syllabus, a page, an announcement)."""
    from kwil.tools.gmail import html_to_text

    return html_to_text(str(markup or "")[:MAX_HTML_CHARS])
