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
_CLOSERS = "\"')]}”’"
_TOKEN_BEFORE = re.compile(r"([A-Za-z][A-Za-z.]*)$")


class SentenceSplitter:
    """Incremental sentence splitter for speech.

    `max_chars`: a run this long with no sentence end is split at the last
    comma/semicolon/dash (else the last space), so a rambling sentence
    doesn't hold up the audio."""

    def __init__(self, max_chars: int = 220):
        self.max_chars = max_chars
        self.buffer = ""

    def feed(self, text: str) -> list[str]:
        self.buffer += text
        out = []
        while True:
            cut = self._find_boundary()
            if cut is None:
                cut = self._find_forced_cut()
            if cut is None:
                break
            sentence, self.buffer = self.buffer[:cut], self.buffer[cut:]
            sentence = _clean(sentence)
            if sentence:
                out.append(sentence)
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

    def _find_forced_cut(self) -> int | None:
        text = self.buffer
        if len(text) <= self.max_chars:
            return None
        window = text[: self.max_chars]
        floor = self.max_chars // 3
        for sep in (", ", "; ", " - ", " — ", ": "):
            pos = window.rfind(sep)
            if pos >= floor:
                return pos + len(sep)
        pos = window.rfind(" ")
        return pos + 1 if pos >= floor else self.max_chars


def _clean(sentence: str) -> str:
    """Strip whitespace and markdown leftovers that would be read out."""
    s = re.sub(r"[*_`#]+", "", sentence)
    s = re.sub(r"\s+", " ", s).strip()
    return s if re.search(r"\w", s) else ""


def split_sentences(text: str) -> list[str]:
    """All sentences of a finished text."""
    splitter = SentenceSplitter()
    return splitter.feed(text) + splitter.flush()
