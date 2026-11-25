"""How a filed paper's closing and its stray front lines are read into lines
before ``frame_make`` judges each one. Pure.

MEASURED (2026-11-25, second rerun on the firm's papers): on 6 responses the
closing kept only its Dated line. A pleading closing is laid out with line
breaks AND tabs: "DATED: November 3, 2026<tabs>FIRM NAME", "By:<tab>____",
so one Word line holds two of the closing's lines, and the Dated rule took the
whole rest of the line as the date. Now a closing line is cut at its breaks,
its tab runs and its wide gaps; a Dated value is the date only when it holds no
words but a month; and the signer is the first line with letters after "By:",
however many blank or underscore lines come between.
"""

from __future__ import annotations

import re
from collections.abc import Callable

_MONTHS = (
    "january february march april may june july august september october november december "
    "jan feb mar apr jun jul aug sep sept oct nov dec"
).split()
#: A closing's Dated line: its label and its value.
DATED = re.compile(r"(?i)^(\W*(?:dated\s*:?|date\s*:))\s*(\S.*)$")
#: "By:" alone, with its blank or nothing.
BY_BLANK = re.compile(r"(?i)^by\s*:?\s*[_\s/]*(?:s/)?\s*$")
#: "By: ______ Attorney Name" or "By: /s/ Attorney Name".
BY_NAME = re.compile(r"(?i)^(by\s*:?\s*[_\s]*(?:/s/\s*)?)([A-Za-z][^\d]{2,60})$")
_ONLY_MARKS = re.compile(r"^[\s_\-./]*$")
#: A front line that is an attorney's name with "Esq." or a bar number.
ATTORNEY_LINE = re.compile(
    r"^(?:[A-Z][\w.'’-]*\s+){1,3}[A-Z][\w.'’-]*,?\s*(?:Esq\.?|\(?(?:SBN|State\s+Bar\s+No\.?)\s*[\d#:\s]*\)?)$"
)


def _fold(text: str) -> str:
    return " ".join((text or "").split())


def closing_lines(raw: list[str]) -> list[str]:
    """The closing's lines: each Word paragraph cut at its line breaks, its tab
    runs and its gaps of three spaces or more."""
    out: list[str] = []
    for para in raw:
        for line in para.split("\n"):
            out += [_fold(p) for p in re.split(r"\t+| {3,}", line) if _fold(p)]
    return out


def dated_value(value: str) -> bool:
    """The Dated line's value is only a date: digits, blanks, punctuation and a month."""
    words = [w.lower().rstrip(".") for w in re.findall(r"[A-Za-z]+\.?", value)]
    return all(w in _MONTHS for w in words)


#: A person's name as a signature block prints it: two to five capitalised
#: words, initials allowed, "Esq." or a bar number after it.
PERSON_LINE = re.compile(
    r"^(?:[A-Z][\w.'’-]*,?\s+){1,4}[A-Z][\w.'’-]*,?"
    r"(?:\s*(?:Esq\.?|\(?(?:SBN|State\s+Bar\s+No\.?)\s*[\d#:\s]*\)?))?$"
)
#: The line that ends a signature block: "Attorneys for Plaintiff, …".
ATTORNEYS_FOR = re.compile(r"(?i)^attorneys?\s+for\b")


def firm_attorneys(lines: list[str], identity_only: Callable[[str], bool]) -> tuple[list[str], set[int]]:
    """(the lines, the indexes of the firm's own attorneys in them). Every
    person-name line between the firm's line or "By:" and "Attorneys for" is
    one of the firm's attorneys, never a case fact (2026-11-25: every response
    paper stopped on its second attorney's name). The first is the signer,
    ``[[v.signer]]``, when no line of the block already is; the others are kept
    as the firm wrote them."""
    out = list(lines)
    firm: set[int] = set()
    start: int | None = None
    for i, line in enumerate(out):
        if start is None and (BY_BLANK.match(line) or BY_NAME.match(line) or identity_only(line)):
            start = i
        elif ATTORNEYS_FOR.match(line) and start is not None:
            names = [j for j in range(start + 1, i) if PERSON_LINE.match(out[j]) and not ATTORNEYS_FOR.match(out[j])]
            if names and not any("[[v.signer]]" in out[j] for j in range(start, i)):
                out[names[0]] = "[[v.signer]]"
                names = names[1:]
            firm |= set(names)
            start = None
    return out, firm


def signer_lines(lines: list[str], identity_only: Callable[[str], bool]) -> list[str]:
    """The closing's lines with the signing attorney's name a marked place: the
    name after "By:" on its own line, or the first line with letters after a
    bare "By:". A line that is the firm's own identity is never the signer."""
    out: list[str] = []
    waiting = False
    for line in lines:
        named = BY_NAME.match(line)
        if named and not BY_BLANK.match(line) and not identity_only(named.group(2)):
            out.append(named.group(1).rstrip() + " [[v.signer]]")
            waiting = False
            continue
        if BY_BLANK.match(line):
            waiting = True
        elif waiting and not _ONLY_MARKS.match(line):
            waiting = False
            if len(line.split()) <= 6 and not re.search(r"\d", line) and not identity_only(line):
                line = "[[v.signer]]"
        out.append(line)
    return out


__all__ = [
    "ATTORNEYS_FOR",
    "ATTORNEY_LINE",
    "DATED",
    "PERSON_LINE",
    "closing_lines",
    "dated_value",
    "firm_attorneys",
    "signer_lines",
]
