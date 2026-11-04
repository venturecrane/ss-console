"""The brief as sections: a heading line and the text under it, in order.

The preamble (everything before the first heading: the caption, the title) is
section ``s0`` and has no heading. Every markdown heading (``#`` to ``######``)
opens the next section, so a sub-heading is its own section and an edit to one
leaves its siblings byte for byte. ``join(split(md)) == md`` exactly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$")
DOCUMENT = "document"
_ROMAN = re.compile(r"^(?:[ivxlcdm]+|[a-z]|\d+)[.)]\s+", re.I)


@dataclass
class Section:
    id: str
    heading: str  # the heading line as written ("" for the preamble)
    level: int
    body: str  # everything after the heading line, newline-terminated pieces kept

    @property
    def title(self) -> str:
        m = HEADING.match(self.heading)
        return m.group(2).strip() if m else ""

    @property
    def text(self) -> str:
        return self.heading + self.body


def split(md: str) -> list[Section]:
    lines = md.splitlines(keepends=True)
    out: list[Section] = []
    heading, level, body, n = "", 0, [], 0
    for ln in lines:
        m = HEADING.match(ln.rstrip("\n"))
        if m:
            out.append(Section(f"s{n}", heading, level, "".join(body)))
            n += 1
            heading, level, body = ln, len(m.group(1)), []
        else:
            body.append(ln)
    out.append(Section(f"s{n}", heading, level, "".join(body)))
    return out


def join(sections: list[Section]) -> str:
    return "".join(s.text for s in sections)


def norm_title(s: str) -> str:
    """A heading compared as words: no ``#``, no leading numeral, no
    punctuation, no case. "IV. STATEMENT OF FACTS" == "statement of facts"."""
    t = re.sub(r"^#+\s*", "", str(s or "").strip())
    t = re.sub(r"[*_`]", "", t)
    t = _ROMAN.sub("", t)
    return " ".join(re.sub(r"[^\w\s]", " ", t).lower().split())


def find(sections: list[Section], target: str) -> list[Section]:
    """The sections a target names: ``document`` is every headed section;
    otherwise the one whose title equals the target as words, else the one
    whose title contains it, else none (never a guess among several)."""
    if norm_title(target) == DOCUMENT:
        return [s for s in sections if s.heading]
    want = norm_title(target)
    if not want:
        return []
    exact = [s for s in sections if s.heading and norm_title(s.title) == want]
    if exact:
        return exact[:1]
    part = [s for s in sections if s.heading and want in norm_title(s.title)]
    return part if len(part) == 1 else []


def align(prior: list[Section], md: str) -> list[Section]:
    """A revised brief written elsewhere (a hand revision), cut at the PRIOR
    brief's headings so each prior section id maps to its revised text: a new
    sub-heading folds into the section it was added under. A prior heading the
    revision lacks has no section (``invariants`` names it missing)."""
    lines = md.splitlines(keepends=True)
    titles = {norm_title(s.title): s for s in prior if s.heading}
    out: list[Section] = []
    cur = Section(prior[0].id, "", 0, "") if prior else Section("s0", "", 0, "")
    taken: set[str] = set()
    for ln in lines:
        m = HEADING.match(ln.rstrip("\n"))
        hit = titles.get(norm_title(m.group(2))) if m else None
        if hit is not None and hit.id not in taken:
            out.append(cur)
            taken.add(hit.id)
            cur = Section(hit.id, ln, hit.level, "")
        else:
            cur.body += ln
    out.append(cur)
    return out


def paragraphs(body: str) -> list[str]:
    """Prose paragraphs: blank-line separated blocks that are not tables,
    headings, list items or markers alone."""
    out = []
    for block in re.split(r"\n\s*\n", body):
        b = block.strip()
        if not b or b.startswith("|") or HEADING.match(b.splitlines()[0]):
            continue
        out.append(b)
    return out


_SENTENCE = re.compile(r"(?<!\b[A-Z])(?<!\bv)(?<!\bNo)(?<!\bpp)(?<!\bp)(?<!\bDep)(?<!\bEx)[.!?](?=\s+[A-Z(\"'])")


def sentences(body: str) -> list[str]:
    out = []
    for p in paragraphs(body):
        out += [s.strip() for s in _SENTENCE.split(" ".join(p.split())) if s.strip()]
    return out


def words(body: str) -> int:
    return sum(len(p.split()) for p in paragraphs(body))


def tables(body: str) -> list[str]:
    """Each markdown table (a run of lines starting ``|``), verbatim."""
    out, cur = [], []
    for ln in body.splitlines(keepends=True):
        if ln.lstrip().startswith("|"):
            cur.append(ln)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return out
