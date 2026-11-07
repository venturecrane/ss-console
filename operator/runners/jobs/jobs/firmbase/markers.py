"""Marked places read from a paragraph's whole text, so a save in Word cannot hide one,
and read only from structure Word has been seen to write.

``base.slot_names`` reads a marked place, ``[[name]]``, only when it sits in ONE
text run. Word splits a paragraph's runs when it saves (a spelling mark, an
edit to part of the line, a revision id), so a firm that opens its library file
and saves it can leave ``[[`` + ``name`` + ``]]`` in three runs with a proofing
mark between (design 2026-11-06, piece 4).

Two readers:

* :func:`slot_names`: what the text says. Each paragraph's own text with its
  runs joined, matched against the marker pattern. For a paragraph the
  single-run reader reads, it returns exactly what that reader returns.
* :func:`marked`: what can be written into, the reader firmbase uses. It reads a
  paragraph's structure as well as its text (:func:`read`), DEFAULT-DENY: inside
  a paragraph that holds a marked place, only what :data:`CONTENT` and
  :data:`IN_RUN` list may stand, each entry proven by a fixture saved by Word
  (``tests/fixtures/word_saved_markers/``). Anything else makes the paragraph
  unreadable and the file one that cannot be written into (:func:`unwritable`),
  with a reason naming what was found. It never guesses.

Three decided rules (2026-11-06):

* A tab, a line break or a no-break hyphen between a marker's first and last
  character is not a marked place: a person sees two pieces, or a character the
  marker does not have. It reads as no marker; a plan that needs it is refused
  by the fit check ("the base has no slot").
* Text in a pending tracked deletion is not part of the line. A deletion
  between the marker's first and last character is an unfinished change in it,
  and refuses the same way an insertion does.
* A pending tracked insertion anywhere in a marked paragraph refuses: which
  text is there is the firm's decision until the change is accepted or rejected.
  So does hidden text on the marker, all of it or part.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
SLOT = re.compile(r"^\[\[([a-z0-9.]+)\]\]$")
#: Every child the schema gives a run's properties (``rPrChange`` aside: a tracked
#: format change is an unfinished change and refuses).
RPR = (
    "rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike", "outline", "shadow",
    "emboss", "imprint", "noProof", "snapToGrid", "vanish", "webHidden", "color", "spacing", "w", "kern",
    "position", "sz", "szCs", "highlight", "u", "effect", "bdr", "shd", "fitText", "vertAlign", "rtl", "cs", "em",
    "lang", "eastAsianLayout", "specVanish", "oMath",
)  # fmt: skip

_P = f"{W}p"
_T = f"{W}t"

#: What may stand in a marked paragraph's content (and in the content of the
#: wrappers here), with the Word-saved fixture that proves Word writes it there.
CONTENT: dict[str, str] = {
    "pPr": "one-proof-mark-spelling (and most others)",
    "r": "every fixture",
    "proofErr": "one-proof-mark-spelling, two-proof-mark-grammar",
    "bookmarkStart": "eight-bookmark-start",
    "bookmarkEnd": "nine-bookmark-end",
    "smartTag": "fifteen-smart-tag-on-part (nested, no smartTagPr)",
    "hyperlink": "eighteen-hyperlink-on-part",
    "sdt": "twenty-content-control-on-part (sdtPr and sdtEndPr empty, as the fixture holds them)",
    "del": "sixteen-tracked-insertion, seventeen-tracked-deletion",
    "ins": "sixteen-tracked-insertion, seventeen-tracked-deletion (read, and always refused)",
}
#: What may stand in a run of a marked paragraph, with the fixture that proves it.
IN_RUN: dict[str, str] = {
    "rPr": "one-proof-mark-spelling (its children: RPR, the schema's own list)",
    "t": "every fixture",
    "tab": "eleven-tab-in-line, eighteen-hyperlink-on-part",
    "br": "twelve-line-break-in-line, thirteen-two-texts-one-run",
    "noBreakHyphen": "fourteen-no-break-hyphen",
    "lastRenderedPageBreak": "ten-rendered-page-break",
    "fldChar": "nineteen-field-on-part",
    "instrText": "nineteen-field-on-part",
    "delText": "seventeen-tracked-deletion (inside a deletion only)",
}
#: Inside a run, what a person sees that is not text.
_VISIBLE = {"tab", "br", "noBreakHyphen"}
_CHANGE = (
    "has an unfinished change by the firm ({what}): which text is there is the firm's decision until "
    "the change is accepted or rejected in Word"
)
_HIDDEN = "is hidden text: a marker nobody can see is not a place the firm put there on purpose"
#: Elements no Word-saved fixture holds, refused by name with the plain reason.
_NAMED = {
    "moveFrom": "holds a tracked move (w:moveFrom)",
    "moveTo": "holds a tracked move (w:moveTo)",
    "moveFromRangeStart": "holds a tracked move (w:moveFromRangeStart)",
    "moveFromRangeEnd": "holds a tracked move (w:moveFromRangeEnd)",
    "moveToRangeStart": "holds a tracked move (w:moveToRangeStart)",
    "moveToRangeEnd": "holds a tracked move (w:moveToRangeEnd)",
    "customXml": "is wrapped in custom XML (w:customXml)",
    "commentRangeStart": "has a comment anchored in it (w:commentRangeStart)",
    "commentRangeEnd": "has a comment anchored in it (w:commentRangeEnd)",
    "commentReference": "has a comment anchored in it (w:commentReference)",
    "fldSimple": "holds a field (w:fldSimple) whose text Word can rewrite",
    "ins": _CHANGE.format(what="a tracked insertion"),
    "rPrChange": _CHANGE.format(what="a tracked format change"),
    "pPrChange": _CHANGE.format(what="a tracked paragraph change"),
}


def _local(el: Any) -> str:
    """A WordprocessingML element's local name; another vocabulary's with its prefix's place held."""
    tag = el.tag
    return tag[len(W) :] if tag.startswith(W) else "other:" + tag.rsplit("}", 1)[-1]


def _unknown(name: str) -> str:
    shown = name.replace("other:", "an element of another vocabulary, ") if name.startswith("other:") else f"w:{name}"
    return _NAMED.get(name) or f"holds an element no Word-saved fixture shows there ({shown})"


@dataclass
class _Atom:
    kind: str  # text | visible | field-begin | field-separate | field-end | instr | del
    run: Any = None
    hidden: bool = False
    text: str = ""


@dataclass
class _Walk:
    atoms: list[_Atom] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Reading:
    """A paragraph read for writing. ``names``: its marked places (a refused one
    included, so the fill refuses it by name). ``runs``: the runs holding the
    marker's text, in order. ``merge``: the marker must be merged into its first
    run before a fill (it is split, or not where the single-run fill looks).
    ``fields``: the runs of a field inside the marker, which go with it.
    ``refusal``: why the paragraph cannot be written into."""

    names: tuple[str, ...] = ()
    runs: tuple[Any, ...] = ()
    merge: bool = False
    fields: tuple[Any, ...] = ()
    refusal: str = ""


NONE = Reading()


def _hidden(rpr: Any) -> bool:
    for tag in ("vanish", "specVanish"):
        el = rpr.find(f"{W}{tag}") if rpr is not None else None
        if el is not None and el.get(f"{W}val", "true") not in ("0", "false", "off"):
            return True
    return False


def _check_rpr(rpr: Any, walk: _Walk) -> None:
    for c in rpr:
        if isinstance(c.tag, str) and _local(c) not in RPR:
            walk.problems.append(_unknown(_local(c)))


def _walk_run(r: Any, walk: _Walk, deleted: bool) -> None:
    rpr = r.find(f"{W}rPr")
    hidden = _hidden(rpr)
    for c in r:
        if not isinstance(c.tag, str):
            continue
        name = _local(c)
        misplaced = (name == "delText" and not deleted) or (name == "t" and deleted)
        if name not in IN_RUN or misplaced:
            walk.problems.append(_unknown(name))
        elif name == "rPr":
            _check_rpr(c, walk)
        elif deleted or name == "lastRenderedPageBreak":
            continue
        elif name == "t":
            walk.atoms.append(_Atom("text", r, hidden, c.text or ""))
        elif name in _VISIBLE:
            walk.atoms.append(_Atom("visible", r, text=name))
        elif name == "fldChar":
            walk.atoms.append(_Atom("field-" + c.get(f"{W}fldCharType", ""), r))
        else:
            walk.atoms.append(_Atom("instr", r))


def _check_ppr(ppr: Any, walk: _Walk) -> None:
    for c in ppr.iter():
        if isinstance(c.tag, str) and _local(c) in ("ins", "del", "moveFrom", "moveTo", "pPrChange", "rPrChange"):
            name = _local(c)
            what = "a tracked change to the paragraph mark" if name in ("ins", "del") else None
            walk.problems.append(_CHANGE.format(what=what) if what else _unknown(name))


def _walk(container: Any, walk: _Walk, deleted: bool = False) -> None:
    for el in container:
        if not isinstance(el.tag, str):
            continue
        name = _local(el)
        if name not in CONTENT or (deleted and name != "r"):
            walk.problems.append(_unknown(name))
        elif name == "pPr":
            _check_ppr(el, walk)
        elif name == "r":
            _walk_run(el, walk, deleted)
        elif name in ("smartTag", "hyperlink"):
            _walk(el, walk)
        elif name == "sdt":
            _walk_sdt(el, walk)
        elif name == "del":
            walk.atoms.append(_Atom("del"))
            _walk(el, walk, deleted=True)
        elif name == "ins":
            walk.problems.append(_unknown("ins"))
            _walk(el, walk)


def _walk_sdt(sdt: Any, walk: _Walk) -> None:
    for part in sdt:
        if not isinstance(part.tag, str):
            continue
        name = _local(part)
        if name == "sdtContent":
            _walk(part, walk)
        elif name not in ("sdtPr", "sdtEndPr"):
            walk.problems.append(_unknown(name))
        elif len(part):
            # The fixture's properties are empty; a control's real properties
            # (a binding, a placeholder, a lock) can change what text shows.
            walk.problems.append(f"holds a content control with properties no Word-saved fixture shows (w:{name})")


# ---- the text, joined -----------------------------------------------------------------
def _nearest_paragraph(el: Any) -> Any:
    node = el.getparent()
    while node is not None and node.tag != _P:
        node = node.getparent()
    return node


def own_texts(paragraph: Any) -> list[Any]:
    """The paragraph's own text elements, in order: not those of a paragraph
    nested inside it (a text box)."""
    return [t for t in paragraph.iter(_T) if _nearest_paragraph(t) is paragraph]


def paragraph_text(paragraph: Any) -> str:
    """The paragraph's own text with its runs joined."""
    return "".join(t.text or "" for t in own_texts(paragraph))


def _paragraph_names(paragraph: Any) -> list[str]:
    texts = own_texts(paragraph)
    single = [m.group(1) for t in texts for m in [SLOT.match(t.text or "")] if m]
    if single:
        return single
    whole = SLOT.match("".join(t.text or "" for t in texts))
    return [whole.group(1)] if whole else []


def _paragraphs(el: Any) -> list[Any]:
    return ([el] if el.tag == _P else []) + [p for p in el.iter(_P) if p is not el]


def slot_names(el: Any) -> list[str]:
    """The marked places an element's paragraphs hold, in order, read from
    each paragraph's whole text."""
    return [name for p in _paragraphs(el) for name in _paragraph_names(p)]


# ---- the reading for writing ----------------------------------------------------------
def _field_runs(atoms: list[_Atom], first: int, last: int) -> tuple[tuple[Any, ...], str]:
    """(the runs of each field that begins inside the marker, why the fields refuse)."""
    depth, runs, inside = 0, [], False
    for i, a in enumerate(atoms):
        if i == first and depth:
            return (), "sits inside a field: Word can rewrite it"
        if a.kind == "field-begin":
            if depth:
                return (), "holds a field inside a field"
            depth, inside = 1, first < i <= last
        if inside and a.kind.startswith(("field", "instr")) and a.run not in runs:
            runs.append(a.run)
        if a.kind == "field-end" and depth:
            depth, inside = 0, False
    return tuple(runs), ("holds a field that does not end in the line" if depth else "")


def _solid(a: _Atom) -> bool:
    """Anything but an empty text piece."""
    return a.kind != "text" or bool(a.text)


def _shape(atoms: list[_Atom], first: int, last: int) -> str:
    """What refuses in the span from the marker's first character to its last, and
    in its first and last runs."""
    span = atoms[first : last + 1]
    texts = [a for a in span if a.kind == "text" and a.text]
    if any(a.kind == "del" for a in span):
        return _CHANGE.format(what="a tracked deletion inside the marker")
    if all(a.hidden for a in texts):
        return _HIDDEN
    if any(a.hidden for a in texts):
        return "is partly hidden text"
    head, tail = atoms[first].run, atoms[last].run
    # A tab before the marker in its first run stays on the filled text, as it does
    # for a marker in one run; nothing else may share the marker's first or last run.
    if any(a.run is head and _solid(a) and a.text != "tab" for a in atoms[:first]):
        return "shares its first run with what comes before it"
    if any(a.run is tail and _solid(a) for a in atoms[last + 1 :]):
        return "shares its last run with what comes after it"
    return ""


def _merge_reading(names: list[str], walk: _Walk) -> Reading:
    atoms = walk.atoms
    at = [i for i, a in enumerate(atoms) if a.kind == "text" and a.text]
    first, last = at[0], at[-1]
    if any(a.kind == "visible" for a in atoms[first:last]):
        return NONE  # a tab, a break or a no-break hyphen in the marker: two pieces, no marker
    fields, why = _field_runs(atoms, first, last)
    why = why or _shape(atoms, first, last)
    runs = tuple(dict.fromkeys(atoms[i].run for i in at))
    return Reading(tuple(names), runs, True, tuple(r for r in fields if r not in runs), why)


def _legacy(p: Any, runs: tuple[Any, ...]) -> bool:
    """The marker where the single-run fill already writes it: the paragraph's first
    run with text, a child of the paragraph, holding the marker in its one text."""
    first = next((r for r in p.findall(f"{W}r") if r.find(_T) is not None), None)
    return first is not None and len(runs) == 1 and runs[0] is first and len(first.findall(_T)) == 1


def read(p: Any) -> Reading:
    """The paragraph read for writing (see the module docstring)."""
    names = _paragraph_names(p)
    if not names:
        return NONE
    walk = _Walk()
    _walk(p, walk)
    if walk.problems:
        return Reading(tuple(names), refusal=walk.problems[0])
    texts = [a for a in walk.atoms if a.kind == "text" and a.text]
    single = [a for a in texts if SLOT.match(a.text)]
    whole = SLOT.match("".join(a.text for a in texts)) is not None
    if single and not (whole and len(texts) == 1):
        # Markers in one text each, beside other words: what the single-run fill reads.
        hidden = any(a.hidden for a in single)
        return Reading(tuple(names), tuple(a.run for a in single), refusal=_HIDDEN if hidden else "")
    reading = _merge_reading(names, walk)
    if reading.refusal or reading is NONE or reading.fields or not _legacy(p, reading.runs):
        return reading
    return Reading(reading.names, reading.runs)


def marked(el: Any) -> list[str]:
    """The marked places an element's paragraphs hold, in order, read for writing:
    a marker that is not one by the rules above is not listed."""
    return [name for p in _paragraphs(el) for name in read(p).names]


def unwritable(el: Any) -> list[str]:
    """Why the element's file cannot be written into, one reason per refused line."""
    out = []
    for p in _paragraphs(el):
        reading = read(p)
        if reading.refusal:
            out.append(f"the marked line [[{reading.names[0]}]] {reading.refusal}")
    return out


__all__ = ["CONTENT", "IN_RUN", "Reading", "marked", "own_texts", "paragraph_text", "read", "slot_names", "unwritable"]
