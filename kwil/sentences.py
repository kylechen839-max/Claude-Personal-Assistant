"""Split a streaming reply into sentences, so each can be spoken as soon as
it is complete (voice conversation mode).

`SentenceSplitter.feed(delta)` returns the sentences finished so far; a
boundary is only decided once the character after it has arrived, so
"3:30 p.m. tomorrow" and "Dr. Lee" stay whole while "at 3 PM. Then" splits.
`flush()` returns whatever is left at the end of the reply.
"""

from __future__ import annotations

import re

# Never end a sentence: titles and the like are followed by a name.
NEVER_END = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "mt", "vs", "no",
    "fig", "gen", "sen", "rep", "gov", "lt", "col", "sgt", "capt", "rev",
    "e.g", "i.e", "approx", "dept", "ave", "blvd", "rd", "hwy", "ft", "apt",
    "u.s", "u.k", "u.n", "d.c",
}
# End a sentence only when a capital letter follows: "at 3 p.m. Then".
END_IF_CAPITAL = {
    "a.m", "p.m", "am", "pm", "etc", "inc", "ltd", "co", "corp", "jan", "feb",
    "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    "mon", "tue", "tues", "wed", "thu", "thurs", "fri", "sat", "sun",
}
_CLOSERS = "\"')]}”’」』）"
# Chinese full-width ends: a sentence ends there without a following space.
_ZH_ENDS = "。！？；"
_CJK = re.compile(r"[\u3400-\u9fff]")
_ZH_CLAUSE = re.compile(r"[，、；]")
# ", " or "; " or a spaced dash, followed by a word (not "3,000" or "1, 2")
_CLAUSE_BREAK = re.compile(r"(?<!\d)[,;](?= +[A-Za-z])|(?<= ) ?[-–—](?= +[A-Za-z])")
_TOKEN_BEFORE = re.compile(r"([A-Za-z][A-Za-z.]*)$")


class SentenceSplitter:
    """Incremental sentence splitter for speech.

    `max_chars`: a run this long with no sentence end is split at the last
    comma/semicolon/dash (else the last space), so a rambling sentence
    doesn't hold up the audio."""

    def __init__(self, max_chars: int = 220, first_clause_words: int = 4):
        self.max_chars = max_chars
        # The reply's first piece also ends at a clause break (comma,
        # semicolon, dash) once it has this many words, so the first audio
        # renders sooner (Kokoro's time grows with length). 0 = off.
        self.first_clause_words = first_clause_words
        self.buffer = ""
        self._first = True

    def feed(self, text: str) -> list[str]:
        self.buffer += text
        out = []
        while True:
            cut = self._find_boundary()
            if self._first:  # whichever comes first: sentence or clause end
                clause = self._find_first_clause()
                if clause is not None and (cut is None or clause < cut):
                    cut = clause
            if cut is None:
                cut = self._find_forced_cut()
            if cut is None:
                break
            sentence, self.buffer = self.buffer[:cut], self.buffer[cut:]
            sentence = _clean(sentence)
            if sentence:
                out.append(sentence)
                self._first = False
        return out

    def flush(self) -> list[str]:
        rest, self.buffer = _clean(self.buffer), ""
        return [rest] if rest else []

    def _find_boundary(self) -> int | None:
        text = self.buffer
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == "\n":
                return i + 1
            if ch in _ZH_ENDS:
                end = i + 1
                while end < len(text) and text[end] in _ZH_ENDS + "!?…":
                    end += 1  # "？！"
                while end < len(text) and text[end] in _CLOSERS:
                    end += 1
                if end >= len(text):
                    return None  # a closing quote may still come
                return end
            if ch in ".!?…":
                j = i
                while j + 1 < len(text) and text[j + 1] in ".!?…":
                    j += 1  # "?!", "..."
                end = j + 1
                while end < len(text) and text[end] in _CLOSERS:
                    end += 1
                if end >= len(text):
                    return None  # need the next character to decide
                if not text[end].isspace():
                    if text[j] in "!?" and _CJK.match(text[end]):
                        return end  # "好!明天见" (half-width in Chinese)
                    i = end  # "3.5", "example.com", "U.S."
                    continue
                k = end
                while k < len(text) and text[k] in " \t":
                    k += 1
                if k >= len(text):
                    return None  # need the next word to decide
                if text[k] == "\n" or self._ends_sentence(text, i, j, text[k]):
                    return end
                i = end
                continue
            i += 1
        return None

    @staticmethod
    def _ends_sentence(text: str, i: int, j: int, following: str) -> bool:
        if text[i] in "!?" or text[j] in "!?":
            return True
        if text[i:j + 1] in ("...", "…"):
            return following.isupper() or following.isdigit()
        m = _TOKEN_BEFORE.search(text[:i])
        if m is None:
            return True  # after a number or a quote: "I counted 42. Then"
        token = m.group(1).lower().rstrip(".")
        if token in NEVER_END:
            return False
        if len(token) == 1 and m.group(1).isupper():
            return False  # an initial: "J. Smith"
        if token in END_IF_CAPITAL or "." in token:
            return following.isupper()
        return True

    def _find_first_clause(self) -> int | None:
        if not self.first_clause_words:
            return None
        for m in _CLAUSE_BREAK.finditer(self.buffer):
            if len(self.buffer[:m.start()].split()) >= self.first_clause_words:
                return m.end()
        for m in _ZH_CLAUSE.finditer(self.buffer):  # Chinese: ~2 characters a word
            if len(_CJK.findall(self.buffer[:m.start()])) >= 2 * self.first_clause_words:
                return m.end()
        return None

    def _find_forced_cut(self) -> int | None:
        text = self.buffer
        if len(text) <= self.max_chars:
            return None
        window = text[: self.max_chars]
        floor = self.max_chars // 3
        for sep in (", ", "; ", " - ", " — ", ": ", "，", "；", "、"):
            pos = window.rfind(sep)
            if pos >= floor:
                return pos + len(sep)
        pos = window.rfind(" ")
        return pos + 1 if pos >= floor else self.max_chars


_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]+")
_BULLET = re.compile(r"^\s*(?:[-•]|\d+[.)])\s+")


def _clean(sentence: str) -> str:
    """Strip whitespace, markdown, URLs and emoji that would be read out."""
    s = _URL.sub("", sentence)
    s = _EMOJI.sub("", s)
    s = _BULLET.sub("", s)
    s = re.sub(r"[*_`#]+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s if re.search(r"\w", s) else ""


_MD_LINK = re.compile(r"\[([^\]\n]{1,200})\]\((?:https?://|www\.)[^)\s]*\)", re.IGNORECASE)


def without_urls(text: str) -> str:
    """`text` for speech: a markdown link becomes its words and a bare web
    address goes (the phone and the PC never read one out; the sources
    list shows them)."""
    text = _MD_LINK.sub(r"\1", str(text or ""))
    return re.sub(r"[ \t]{2,}", " ", _URL.sub("", text))


def split_sentences(text: str) -> list[str]:
    """All sentences of a finished text."""
    splitter = SentenceSplitter(first_clause_words=0)  # whole sentences only
    return splitter.feed(text) + splitter.flush()
