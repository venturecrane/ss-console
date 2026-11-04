"""Notes the pipeline writes about its own reading, which must never reach the
firm's document.

The map pass sometimes marks a page whose text runs on to the next with a
parenthetical of its own, "(text continues beyond the page)", and on
2026-10-13 one reached an added entry of a real update. It is a statement
about our extraction, not about the record. `strip` removes every such note
before audit and render; `found` is what a proof fails on.
"""

from __future__ import annotations

import re

MARKER = re.compile(
    r"\s*[\(\[](?:the\s+)?(?:text|note|record|entry|sentence|page|content|report|transcription)\b[^()\[\]\n]{0,60}?"
    r"\b(?:continues|continued|cut off|truncated|incomplete|ends mid|runs on|beyond (?:the|this) page)"
    r"[^()\[\]\n]{0,60}[\)\]]",
    re.I,
)


def found(text: str | None) -> list[str]:
    return [m.group(0).strip() for m in MARKER.finditer(text or "")]


def strip(text: str) -> str:
    return MARKER.sub("", text)


# ---- the update lane's process notes ---------------------------------------------
# On a real update (2026-10-14 parity run) an added entry's DATE LINE carried
# "(handwritten date, unconfirmed)" and "(log row reads "10-5," no year
# written; ...)", and its body the map prompt's "[NTD: ... we were unable to
# annotate or decipher ...]". Each is about how the chronology was made. The
# update review graded them MUST-FIX, its repair cannot rewrite a date line,
# and the job held for SMD after four paid rounds. In the update lane they go
# to the job report (``process_notes``), never to the firm. (The full lane's
# own NTD house note is left to that lane: ``strip`` is unchanged.)
_PROCESS = (
    r"unconfirmed|log row|row reads|no year written|"
    r"unable to (?:annotate|decipher|read)|could not (?:be )?(?:read|deciphered)|NTD\b"
)
_DATE_LINE_NOTE = re.compile(rf"\s*[;,]?\s*[\(\[][^()\[\]\n]*?\b(?:{_PROCESS})[^()\[\]\n]*[\)\]]", re.I)
_BODY_NOTE = re.compile(
    r"\s*\((?!Exhibit\b)[^()\[\]\n]*?\b(?:log row reads|unable to (?:annotate|decipher)|"
    r"could not (?:be )?deciphered)\b[^()\[\]\n]*\)",
    re.I,
)
_NTD = re.compile(r"\[NTD:[^\]]*\]")
_CITE = re.compile(r"\(Exhibit (\d+)(?: - p\. ([0-9,\s\-]+))?\)")
_ABOUT_ILLEGIBLE = re.compile(r"(?i)illegib|decipher|not legible|unable to read")
_GONE = "\x00"
#: What an illegibility note becomes when its citation would otherwise go:
#: a fact about the record, audited against its page like any claim.
ILLEGIBLE = "Portions of this record are handwritten and not legible."


def _pages(spec: str | None) -> set[int]:
    out: set[int] = set()
    for part in (spec or "1").split(","):
        a, _, b = part.strip().partition("-")
        if a.isdigit():
            out |= set(range(int(a), int(b) + 1)) if b.isdigit() else {int(a)}
    return out


def _cites(text: str) -> set[tuple[int, int]]:
    return {(int(m.group(1)), p) for m in _CITE.finditer(text) for p in _pages(m.group(2))}


def _only_cites(line: str) -> bool:
    return bool(_CITE.search(line)) and not _CITE.sub("", line).strip(" .;,")


def _line(ln: str, elsewhere: set[tuple[int, int]], illegible: bool) -> str | None:
    """A line a note was taken out of: what remains, None when nothing does."""
    new = re.sub(r" ([.,;])", r"\1", re.sub(r"[ \t]{2,}", " ", ln.replace(_GONE, " "))).strip()
    if not new or (_only_cites(new) and _cites(new) <= elsewhere):
        return None
    if _only_cites(new):
        return f"{ILLEGIBLE} {new}" if illegible else None
    return new


def _block(block: str, others: str) -> tuple[str | None, list[str]]:
    """One body block (a subsection) without its process notes."""
    notes = [m.group(0) for m in _NTD.finditer(block)] + [m.group(0).strip() for m in _BODY_NOTE.finditer(block)]
    if not notes:
        return block, []
    illegible = any(_ABOUT_ILLEGIBLE.search(n) for n in notes)
    lines = _BODY_NOTE.sub(_GONE, _NTD.sub(_GONE, block)).split("\n")
    kept: list[str] = []
    for i, ln in enumerate(lines):
        if _GONE not in ln:
            kept.append(ln)
            continue
        rest = others + "\n" + "\n".join(x for j, x in enumerate(lines) if j != i)
        got = _line(ln, _cites(rest), illegible)
        if got is not None:
            kept.append(got)
    if not any(_CITE.search(x) for x in kept):
        return None, notes  # a subsection heading left with nothing under it
    return "\n".join(kept), notes


def process_notes(entry: str) -> tuple[str, list[str]]:
    """(the entry without the pipeline's notes about its own reading, the
    notes taken out, for the job report).

    The date line loses every such note. In the body an ``[NTD: ...]`` note
    and a parenthetical about our reading go, and whatever else the line says
    stays: legible text is never dropped with a note. A line left holding only
    its citation goes when the entry cites those pages elsewhere; otherwise an
    illegibility note becomes ``ILLEGIBLE`` before its citation, so the page
    stays cited. A subsection left with no cited line goes with it."""
    head, nl, rest = entry.partition("\n")
    notes = [m.group(0).strip(" ;,") for m in _DATE_LINE_NOTE.finditer(head)]
    head = _DATE_LINE_NOTE.sub("", head).rstrip()
    blocks = rest.split("\n\n")
    out: list[str] = []
    for i, b in enumerate(blocks):
        got, n = _block(b, "\n\n".join(x for j, x in enumerate(blocks) if j != i))
        notes += n
        if got is not None:
            out.append(got)
    return head + nl + "\n\n".join(out), notes
