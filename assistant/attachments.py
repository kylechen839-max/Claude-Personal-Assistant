"""Files attached to a chat message in the phone app (the "+" button):
photos, PDFs and plain text files, sent to Claude with the next typed or
push-to-talk message.

Every file is checked here before anything else sees it:
  - at most MAX_ATTACHMENTS per message, each under a size cap (photos:
    [tutor] max_image_mb; PDFs and text: [webapp] max_attachment_mb; the
    whole request is capped by [webapp] max_upload_mb anyway);
  - the type comes from the file's first bytes, never from the name or
    the type the browser declared;
  - photos go through tutor.prepare_image (decoded, shrunk to 1600 px and
    re-encoded, which drops EXIF/GPS and other metadata); PDFs and text
    are passed on as they are.

The result is a list of Anthropic content blocks (image / document) for
ClaudeChat.ask(attachments=...). Nothing is saved: the files live in memory
for the conversation (Starlette may spool a big upload to a temporary file
while the request is read, as with audio), and the conversation history
only keeps a marker (chat.attachment_marker).
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass

MAX_ATTACHMENTS = 4
MAX_TEXT_CHARS = 200_000   # about 50k tokens; longer text files are refused
MAX_NAME_CHARS = 80
MAX_PDF_PAGES = 100        # Claude's limit for a PDF
# What the phone may send, for its error message and the file picker
ALLOWED = ("image/jpeg", "image/png", "image/webp", "image/gif",
           "application/pdf", "text/plain", "text/markdown")
IMAGE_TYPES = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP",
               "image/gif": "GIF"}
TYPES_MESSAGE = "Only photos (JPEG, PNG, WebP, GIF), PDFs and text files can be attached"


# What pdf_pages() looks for: an object's start, and in it the page tree
# root's "/Type /Pages", "/Parent" (only the root has none) and a direct
# "/Count N" (not "/Count 5 0 R"; at most 9 digits, so int() stays cheap).
# The object header's runs are bounded and can't start inside a run of
# digits, so a file of digits or spaces stays linear (unbounded \d+\s+ was
# quadratic: 80 KB of digits took a minute, holding the GIL).
_PDF_TOKENS = re.compile(rb"(?<![0-9])[0-9]{1,10}[ \t\r\n\f\x00]{1,16}[0-9]{1,5}"
                         rb"[ \t\r\n\f\x00]{1,16}obj(?![A-Za-z])"
                         rb"|/Type\s*/Pages(?![A-Za-z])|/Parent(?![A-Za-z])"
                         rb"|/Count\s+(\d{1,9})(?![\d.]|\s+\d+\s+R)")


def pdf_pages(data: bytes) -> int | None:
    """The page count of a PDF from its page tree root (the /Type /Pages
    object without a /Parent): its /Count, the last definition winning. An
    incremental save that adds or removes pages appends a new root; one that
    only annotates pages rewrites those pages but not the root, so they are
    not counted twice. None when the root can't be seen (inside a compressed
    object stream, or an indirect /Count): Claude checks those. One pass,
    match by match: no list of every match, whatever the file holds."""
    pages = None
    is_root = has_parent = False
    count = None
    for m in _PDF_TOKENS.finditer(data):
        token = m.group(0)
        if token.startswith(b"/Type"):
            is_root = True
        elif token.startswith(b"/Parent"):
            has_parent = True
        elif token.startswith(b"/Count"):
            if count is None:
                count = int(m.group(1))
        else:  # the next object starts: was the last one the root?
            if is_root and not has_parent and count is not None:
                pages = count
            is_root = has_parent = False
            count = None
    if is_root and not has_parent and count is not None:
        pages = count
    return pages

# Bytes no text file has (tab, newlines and form feed are fine)
_CONTROL = re.compile(rb"[\x00-\x08\x0b\x0e-\x1f\x7f]")


class AttachmentRejected(ValueError):
    """A friendly reason the upload can't be sent (shown on the phone)."""


@dataclass
class Attachment:
    kind: str          # "image", "pdf" or "text"
    media_type: str    # what is sent to Claude
    data: bytes        # cleaned image bytes, or the PDF / text file as sent
    name: str          # sanitized file name (for the history marker only)

    def block(self) -> dict:
        """The Anthropic content block for this file."""
        if self.kind == "image":
            return {"type": "image", "source": {
                "type": "base64", "media_type": self.media_type,
                "data": base64.b64encode(self.data).decode("ascii")}}
        if self.kind == "pdf":
            return {"type": "document", "title": self.name, "source": {
                "type": "base64", "media_type": "application/pdf",
                "data": base64.b64encode(self.data).decode("ascii")}}
        return {"type": "document", "title": self.name, "source": {
            "type": "text", "media_type": "text/plain",
            "data": self.data.decode("utf-8-sig")}}


def sniff(data: bytes) -> str | None:
    """The media type from the file's first bytes, or None if not allowed."""
    head = data[:16]
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:1024].lstrip(b"\r\n\t \x0c").startswith(b"%PDF-"):
        return "application/pdf"
    if not _CONTROL.search(data):  # text has no control bytes (zip, exe...)
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return None
        return "text/plain"
    return None


def safe_name(name: str | None, fallback: str = "file") -> str:
    """The file's base name without control characters, shortened."""
    base = re.split(r"[\\/]", str(name or ""))[-1]
    base = " ".join("".join(c for c in base if c.isprintable()).split())
    base = base.replace("[", "(").replace("]", ")")  # keeps the marker readable
    if len(base) > MAX_NAME_CHARS:
        stem, dot, ext = base.rpartition(".")
        ext = ext[:10] if dot else ""
        base = base[:MAX_NAME_CHARS - len(ext) - 2] + "…" + ("." + ext if ext else "")
    return base or fallback


def prepare(data: bytes, name: str | None, *, max_image_bytes: int,
            max_file_bytes: int) -> Attachment:
    """Check one uploaded file and return it ready to send."""
    from assistant.tutor import ImageRejected, prepare_image

    if not data:
        raise AttachmentRejected("That file is empty")
    kind = sniff(data)
    if kind is None:
        raise AttachmentRejected(TYPES_MESSAGE)
    clean = safe_name(name, "photo" if kind.startswith("image/") else "file")
    if kind in IMAGE_TYPES:
        if len(data) > max_image_bytes:
            raise AttachmentRejected(
                f"A photo is over {max_image_bytes / 1_000_000:g} MB")
        try:
            image, media_type = prepare_image(data, None, max_image_bytes,
                                              allowed=IMAGE_TYPES)
        except ImageRejected:
            raise AttachmentRejected("A photo couldn't be read") from None
        return Attachment("image", media_type, image, clean)
    if len(data) > max_file_bytes:
        raise AttachmentRejected(f"A file is over {max_file_bytes / 1_000_000:g} MB")
    if kind == "application/pdf":
        if (pdf_pages(data) or 0) > MAX_PDF_PAGES:
            raise AttachmentRejected(f"A PDF is over {MAX_PDF_PAGES} pages")
        return Attachment("pdf", kind, data, clean)
    if len(data.decode("utf-8-sig")) > MAX_TEXT_CHARS:
        raise AttachmentRejected("A text file is too long to send")
    return Attachment("text", "text/plain", data, clean)


def prepare_all(files: list[tuple[str | None, bytes]], *, max_image_bytes: int,
                max_file_bytes: int) -> list[Attachment]:
    """Check every (name, bytes) of one message; raises AttachmentRejected."""
    if len(files) > MAX_ATTACHMENTS:
        raise AttachmentRejected(f"Attach at most {MAX_ATTACHMENTS} files to a message")
    return [prepare(data, name, max_image_bytes=max_image_bytes,
                    max_file_bytes=max_file_bytes) for name, data in files]


def limits(config: dict) -> dict:
    """create_app's per-file caps from config: [tutor] max_image_mb for
    photos, [webapp] max_attachment_mb for PDFs and text, neither above
    [webapp] max_upload_mb (the cap on the whole request)."""
    cap = float(config["webapp"]["max_upload_mb"])
    mb = lambda v: int(min(float(v), cap) * 1_000_000)  # noqa: E731
    return {"max_image_bytes": mb(config["tutor"]["max_image_mb"]),
            "max_attachment_bytes": mb(config["webapp"]["max_attachment_mb"])}


def describe(attachments: list[Attachment]) -> str:
    """For INFO logs: kinds and sizes only, never names or contents."""
    return ", ".join(f"{a.kind} {len(a.data)} B" for a in attachments)
