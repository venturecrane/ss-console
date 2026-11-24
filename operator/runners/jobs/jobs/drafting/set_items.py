"""A propounded set's requests, read and renumbered by code (design B, B1).

A request is its label line ("SPECIAL INTERROGATORY NO. 4", "**REQUEST FOR
ADMISSION NO. 4:**") and its text: the paragraphs up to the next label, or, for
the last request, the one paragraph after its label. A label that carries its
text on the same line ("REQUEST FOR ADMISSION NO. 4: Admit that ...") is that
request alone. The paragraph that follows a set's last request with no label
and no heading (a production set's time-and-place paragraph) is the set's own,
never the request's: nothing here removes or numbers it.

A line that only begins with a request's words is not a label: "Special
Interrogatory No. 41 asked the date" is a sentence, and a label's words are
followed by nothing, or by a colon or period before its text.

``renumber`` numbers the labels of one kind from the set's first label, in
order, and rewrites the set's own references to its requests ("Request for
Admission No. 7") to match. A number two labels shared is not rewritten in a
reference: the reference is named instead, never guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Each variant's label words (``sets.VARIANTS`` keys).
WORDS = {
    "special_interrogatories": r"SPECIAL\s+INTERROGATORY",
    "requests_for_admission": r"REQUEST\s+FOR\s+ADMISSION",
    "requests_for_production": r"REQUEST\s+FOR\s+PRODUCTION(?:\s+OF\s+DOCUMENTS)?",
}
#: What a request's words are called in a sentence for the attorney.
NAMES = {
    "special_interrogatories": ("special interrogatory", "special interrogatories"),
    "requests_for_admission": ("request for admission", "requests for admission"),
    "requests_for_production": ("request for production", "requests for production"),
}


def _label(variant: str) -> re.Pattern[str]:
    return re.compile(rf"(?im)^([^\w\n]*)({WORDS[variant]})(\s+NO\.?\s*)(\d+)(.*)$")


#: What may follow a label's number on its own line: closing marks, then text
#: only after a colon or period.
_ALONE = re.compile(r"^[\s*_)\]]*[:.]?[\s*_]*$")
_INLINE = re.compile(r"^[\s*_)\]]*[:.][\s*_]*\S")
_HEAD = re.compile(r"^\s*(#{1,6}\s|\*\*[^*]+\*\*\s*$|dated\s*:)", re.I)


@dataclass(frozen=True)
class Item:
    number: int
    #: Character offsets of the request in the document: its label line to the
    #: end of its text.
    start: int
    end: int
    #: Offsets of the label's number, the one span ``renumber`` rewrites.
    num_start: int
    num_end: int

    def text(self, doc: str) -> str:
        return doc[self.start : self.end]


def _labels(doc: str, variant: str) -> list[re.Match[str]]:
    return [m for m in _label(variant).finditer(doc) if _ALONE.match(m.group(5)) or _INLINE.match(m.group(5))]


def _last_end(doc: str, m: re.Match[str]) -> int:
    """Where the last request ends: its own line when it carries its text,
    else the one paragraph after the label."""
    if _INLINE.match(m.group(5)):
        return m.end()
    rest = doc[m.end() :]
    para = re.search(r"\S[\s\S]*?(?=\n\s*\n|\Z)", rest)
    if para is None or _HEAD.match(para.group(0)):
        return m.end()
    return m.end() + para.end()


def read(doc: str, variant: str) -> list[Item]:
    """The set's requests of ``variant``, in the document's order."""
    marks = _labels(doc, variant)
    out = []
    for i, m in enumerate(marks):
        if i + 1 < len(marks):
            end = len(doc[: marks[i + 1].start()].rstrip())
        else:
            end = _last_end(doc, m)
        out.append(Item(int(m.group(4)), m.start(), end, m.start(4), m.end(4)))
    return out


def count(doc: str, variant: str) -> int:
    """How many requests of ``variant`` the set carries: its label lines."""
    return len(_labels(doc, variant))


def variant_of(doc: str) -> str | None:
    """The one variant whose labels the set carries; None for none or several."""
    found = [v for v in WORDS if _labels(doc, v)]
    return found[0] if len(found) == 1 else None


def _reference(variant: str) -> re.Pattern[str]:
    """A reference in a sentence to one of the set's own requests ("Request for
    Admission No. 7", "REQUEST FOR ADMISSION NO. 7"); never a label line."""
    return re.compile(rf"(?i)\b({WORDS[variant]})(\s+No\.?\s*)(\d+)\b")


@dataclass
class Renumbered:
    doc: str
    #: The label numbers as they were, in order, and as they are now.
    before: list[int]
    after: list[int]
    #: References rewritten (old number to new), and references left because
    #: the number they cite was carried by two labels or by none.
    remapped: dict[int, int]
    unresolved: list[int]

    @property
    def changed(self) -> bool:
        return self.before != self.after


def renumber(doc: str, variant: str) -> Renumbered:
    """Number the set's labels from its first label, one by one, and its own
    references to them to match. Idempotent: a set already in order comes back
    unchanged."""
    items = read(doc, variant)
    before = [it.number for it in items]
    if not items:
        return Renumbered(doc, [], [], {}, [])
    first = before[0]
    after = list(range(first, first + len(items)))
    if before == after:
        return Renumbered(doc, before, after, {}, [])
    shared = {n for n in before if before.count(n) > 1}
    mapping = {b: a for b, a in zip(before, after) if b not in shared}
    labels = {(it.num_start, it.num_end) for it in items}
    remapped: dict[int, int] = {}
    unresolved: list[int] = []

    def ref(m: re.Match[str]) -> str:
        if (m.start(3), m.end(3)) in labels:
            return m.group(0)
        old = int(m.group(3))
        if old in mapping:
            if mapping[old] != old:
                remapped[old] = mapping[old]
            return f"{m.group(1)}{m.group(2)}{mapping[old]}"
        if old not in unresolved:
            unresolved.append(old)
        return m.group(0)

    doc = _reference(variant).sub(ref, doc)
    # Labels last, from the end, so earlier offsets hold (references never change a label's span).
    for it, new in sorted(zip(read(doc, variant), after), key=lambda p: -p[0].num_start):
        doc = doc[: it.num_start] + str(new) + doc[it.num_end :]
    return Renumbered(doc, before, after, remapped, sorted(unresolved))


def append(doc: str, variant: str, more: str) -> tuple[str, int]:
    """``more`` (requests a top-up pass wrote, each a label and its text) put
    after the set's last request, then the whole set renumbered. Only requests
    of ``variant`` are taken; anything else ``more`` carries is dropped. The
    count added comes back with the document."""
    items = read(doc, variant)
    new = read(more, variant)
    if not items or not new:
        return doc, 0
    block = "\n\n".join(it.text(more).strip() for it in new)
    end = items[-1].end
    doc = doc[:end].rstrip() + "\n\n" + block + doc[end:]
    return renumber(doc, variant).doc, len(new)


def numbers_note(r: Renumbered, variant: str) -> list[str]:
    """What the renumbering did, in the attorney's words; nothing when nothing changed."""
    if not r.changed:
        return []
    plural = NAMES[variant][1]
    moved = [(b, a) for b, a in zip(r.before, r.after) if b != a]
    head = ", ".join(f"former No. {b} is now No. {a}" for b, a in moved[:6])
    more = f", and {len(moved) - 6} more" if len(moved) > 6 else ""
    out = [f"Numbering: the {plural} were numbered again in order ({head}{more})"]
    if r.unresolved:
        nos = ", ".join(str(n) for n in r.unresolved)
        out.append(
            f"Numbering: a request cites No. {nos} of these {plural}, which two requests shared or none carried; "
            "the citation is left as written for your review"
        )
    return out
