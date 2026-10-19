"""Read LaTeX out loud: "$\\frac{x^2}{2}$" -> "x squared over 2".

Used when the tutor's explanation is spoken: the screen shows the LaTeX
(rendered with KaTeX), the voice reads the same maths the way a person
would. Covers the common school forms: fractions, powers, roots,
subscripts, Greek letters, trig and log functions, sums, integrals,
limits, derivatives and the usual relations. Anything unknown is read as
its command name rather than dropped.
"""

from __future__ import annotations

import re

GREEK = {name: name for name in (
    "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi "
    "omicron pi rho sigma tau upsilon phi chi psi omega").split()}
GREEK.update({"varepsilon": "epsilon", "vartheta": "theta", "varphi": "phi",
              "Delta": "delta", "Gamma": "gamma", "Theta": "theta",
              "Lambda": "lambda", "Sigma": "sigma", "Phi": "phi", "Pi": "pi",
              "Omega": "omega", "Psi": "psi"})
FUNCTIONS = {"sin": "sine", "cos": "cosine", "tan": "tangent", "sec": "secant",
             "csc": "cosecant", "cot": "cotangent", "arcsin": "arc sine",
             "arccos": "arc cosine", "arctan": "arc tangent", "sinh": "hyperbolic sine",
             "cosh": "hyperbolic cosine", "tanh": "hyperbolic tangent",
             "ln": "natural log", "log": "log", "exp": "exp", "det": "determinant",
             "max": "max", "min": "min", "gcd": "g c d"}
SYMBOLS = {
    "cdot": "times", "times": "times", "div": "divided by", "pm": "plus or minus",
    "mp": "minus or plus", "le": "is less than or equal to",
    "leq": "is less than or equal to", "ge": "is greater than or equal to",
    "geq": "is greater than or equal to", "neq": "is not equal to",
    "ne": "is not equal to", "approx": "is approximately",
    "equiv": "is equivalent to", "sim": "is similar to", "propto": "is proportional to",
    "infty": "infinity", "to": "approaches", "rightarrow": "goes to",
    "Rightarrow": "implies", "implies": "implies", "iff": "if and only if",
    "in": "in", "notin": "not in", "subset": "subset of", "cup": "union",
    "cap": "intersect", "forall": "for all", "exists": "there exists",
    "partial": "partial", "nabla": "del", "degree": "degrees", "circ": "degrees",
    "angle": "angle", "perp": "is perpendicular to", "parallel": "is parallel to",
    "ldots": "dot dot dot", "cdots": "dot dot dot", "dots": "dot dot dot",
    "hbar": "h bar", "ell": "l", "prime": "prime",
}
IGNORED = {"left", "right", "displaystyle", "textstyle", "big", "Big", "bigg",
           "Bigg", ",", ";", "!", ":", "quad", "qquad", " ", "limits", "nolimits"}
TEXT_COMMANDS = {"text", "mathrm", "textrm", "mathit", "mathbf", "operatorname",
                 "boldsymbol", "mathcal", "mathbb", "vec", "hat", "bar", "overline",
                 "tilde", "dot"}
CHARS = {"+": "plus", "-": "minus", "=": "equals", "<": "is less than",
         ">": "is greater than", "/": "over", "!": "factorial", "'": "prime",
         "|": "absolute value of", ",": ",", "*": "times", "%": "percent"}
ORDINAL_POWERS = {"2": "squared", "3": "cubed"}
ROOTS = {"2": "square root", "3": "cube root", "4": "fourth root"}


class _Reader:
    def __init__(self, src: str):
        self.src = src
        self.i = 0

    def peek(self) -> str:
        return self.src[self.i] if self.i < len(self.src) else ""

    def skip_space(self) -> None:
        while self.peek().isspace():
            self.i += 1

    def command(self) -> str:
        """After a backslash: a letter run, or a single symbol."""
        start = self.i
        if self.peek().isalpha():
            while self.peek().isalpha():
                self.i += 1
        else:
            self.i += 1
        return self.src[start:self.i]

    def group(self) -> str:
        """The raw text of the next {group}, \\command or single character."""
        self.skip_space()
        c = self.peek()
        if c == "{":
            depth, start = 0, self.i
            while self.i < len(self.src):
                ch = self.src[self.i]
                depth += ch == "{"
                depth -= ch == "}"
                self.i += 1
                if depth == 0:
                    return self.src[start + 1:self.i - 1]
            return self.src[start + 1:]
        if c == "\\":
            self.i += 1
            return "\\" + self.command()
        self.i += 1
        return c

    def optional(self) -> str | None:
        self.skip_space()
        if self.peek() != "[":
            return None
        end = self.src.find("]", self.i)
        if end < 0:
            return None
        value, self.i = self.src[self.i + 1:end], end + 1
        return value


def _is_simple(spoken: str) -> bool:
    """One word or number: no 'all over' / 'the quantity' needed."""
    return len(spoken.split()) <= 1 or bool(re.fullmatch(r"[\w.]+ \w+", spoken)
                                            and not re.search(
        r"\b(plus|minus|times|over|equals)\b", spoken))


def _power(base_spoken: str, exponent: str) -> str:
    exp = speak_latex(exponent)
    if exponent.strip() in ORDINAL_POWERS:
        return f"{base_spoken} {ORDINAL_POWERS[exponent.strip()]}"
    if exp == "minus 1":
        return f"{base_spoken} to the minus 1"
    if exponent.strip() in ("\\circ", "\\degree"):
        return f"{base_spoken} degrees"
    if _is_simple(exp):
        return f"{base_spoken} to the {exp}"
    return f"{base_spoken} to the power of {exp},"


def _scripts(r: _Reader) -> tuple[str | None, str | None]:
    """Optional _{lower} and ^{upper}, in either order."""
    lower = upper = None
    for _ in range(2):
        r.skip_space()
        if r.peek() == "_" and lower is None:
            r.i += 1
            lower = r.group()
        elif r.peek() == "^" and upper is None:
            r.i += 1
            upper = r.group()
    return lower, upper


def speak_latex(src: str) -> str:
    """Spoken English for one LaTeX expression (no $ delimiters)."""
    r = _Reader(src)
    words: list[str] = []

    def last_word_pop() -> str:
        return words.pop() if words else ""

    while r.i < len(src):
        c = r.peek()
        if c.isspace():
            r.i += 1
            continue
        if c == "\\":
            r.i += 1
            name = r.command()
            if name in IGNORED:
                continue
            if name in ("frac", "dfrac", "tfrac"):
                num, den = r.group(), r.group()
                if re.fullmatch(r"d(\^\{?\d\}?)?", num.strip()) and \
                        re.fullmatch(r"d\s*\w(\^\{?\d\}?)?", den.strip()):
                    var = re.sub(r"[d\s^{}0-9]", "", den)
                    words.append(f"the derivative with respect to {var} of")
                    continue
                n, d = speak_latex(num), speak_latex(den)
                if _is_simple(n) and _is_simple(d):
                    words.append(f"{n} over {d}")
                elif _is_simple(d):
                    words.append(f"{n}, all over {d}")
                else:
                    words.append(f"the quantity {n}, over the quantity {d},")
                continue
            if name == "sqrt":
                index = r.optional()
                inner = speak_latex(r.group())
                root = ROOTS.get((index or "2").strip(),
                                 f"{speak_latex(index or '')}th root")
                words.append(f"the {root} of {inner}" + ("" if _is_simple(inner)
                                                         else ","))
                continue
            if name in ("sum", "prod", "int", "iint", "oint"):
                lower, upper = _scripts(r)
                what = {"sum": "the sum", "prod": "the product", "int": "the integral",
                        "iint": "the double integral", "oint": "the contour integral"}[name]
                if lower is not None and upper is not None:
                    what += f" from {speak_latex(lower)} to {speak_latex(upper)}"
                elif lower is not None:
                    what += f" over {speak_latex(lower)}"
                words.append(what + " of")
                continue
            if name == "lim":
                lower, _ = _scripts(r)
                words.append(f"the limit as {speak_latex(lower)} of" if lower
                             else "the limit of")
                continue
            if name in TEXT_COMMANDS:
                inner = r.group()
                words.append(speak_latex(inner) if name not in ("text", "mathrm",
                                                                "textrm",
                                                                "operatorname")
                             else inner.strip())
                if name == "vec":
                    words[-1] = f"vector {words[-1]}"
                elif name in ("hat", "bar", "overline"):
                    words[-1] = f"{words[-1]} {'hat' if name == 'hat' else 'bar'}"
                continue
            if name in FUNCTIONS:
                spoken = FUNCTIONS[name]
                lower, upper = _scripts(r)
                if lower is not None:
                    spoken += f" base {speak_latex(lower)}"
                if upper is not None:
                    spoken = (f"{spoken} squared" if upper.strip() == "2"
                              else f"{spoken} to the {speak_latex(upper)}")
                words.append(spoken + " of")
                continue
            words.append(GREEK.get(name) or SYMBOLS.get(name) or name)
            continue
        if c == "{":
            words.append(speak_latex(r.group()))
            continue
        if c == "}":
            r.i += 1
            continue
        if c in "([":
            close = ")" if c == "(" else "]"
            depth, start = 0, r.i
            while r.i < len(src):
                ch = src[r.i]
                depth += ch == c
                depth -= ch == close
                r.i += 1
                if depth == 0:
                    break
            inner = speak_latex(src[start + 1:r.i - 1])
            r.skip_space()
            if r.peek() == "^":
                r.i += 1
                exponent = r.group()
                words.append(_power(f"the quantity {inner}", exponent)
                             if not _is_simple(inner) else _power(inner, exponent))
            else:
                times = "times " if words and words[-1][-1:].isdigit() else ""
                words.append(inner if _is_simple(inner)
                             else f"{times}the quantity {inner},")
            continue
        if c == "^":
            r.i += 1
            words.append(_power(last_word_pop(), r.group()))
            continue
        if c == "_":
            r.i += 1
            sub = speak_latex(r.group())
            words.append(f"{last_word_pop()} sub {sub}")
            continue
        if c.isdigit() or (c == "." and src[r.i + 1:r.i + 2].isdigit()):
            start = r.i
            while r.peek().isdigit() or r.peek() == ".":
                r.i += 1
            words.append(src[start:r.i].rstrip("."))
            continue
        if c.isalpha():
            r.i += 1
            words.append(c)
            continue
        if c in CHARS:
            r.i += 1
            if c == "-" and (not words or words[-1] in ("equals", "plus", "minus",
                                                        "times", "over", ",")):
                words.append("negative")
            elif c == "|":
                end = src.find("|", r.i)
                if end > 0:
                    words.append(f"the absolute value of {speak_latex(src[r.i:end])}")
                    r.i = end + 1
            else:
                words.append(CHARS[c])
            continue
        r.i += 1  # anything else (&, ~, ...) is layout
    text = " ".join(w for w in words if w)
    text = re.sub(r"\s+,", ",", text)
    return re.sub(r"\s+", " ", text).strip(" ,")


# Display maths may span lines; inline $...$ may not, and "$5 and $6"
# (prices) isn't maths: no space after the opening $, none before the
# closing one, and no digit right after it.
_MATH = re.compile(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]|\\\((.+?)\\\)"
                   r"|(?<![\\$])\$(?![\s$])([^$\n]+?)(?<![\s\\])\$(?!\d)",
                   re.DOTALL)


def speakable(text: str) -> str:
    """Text for the voice: maths in $...$, $$...$$, \\(...\\) or \\[...\\]
    read aloud, and markdown marks removed."""
    def repl(m: re.Match) -> str:
        expr = next(g for g in m.groups() if g is not None)
        return speak_latex(expr)

    out = _MATH.sub(repl, text)
    out = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", out)      # headings
    out = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), out)
    out = re.sub(r"(?m)^\s*[-*]\s+", "", out)            # bullets
    out = re.sub(r"(?m)^\s*(\d+)\.\s+", r"Step \1: ", out)
    out = out.replace("`", "")
    return re.sub(r"[ \t]+", " ", out).strip()
