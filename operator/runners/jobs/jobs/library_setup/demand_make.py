"""A firm's demand-letter library file: its house reference, made stockable.
Pure: bytes in, bytes out. No model call.

HOW THE DEMAND JOB READS ITS FILE (``demand/house.py`` ``HouseWriter``, and the
numbered and crabtree writers). The file is a "prototypes" file: one of the
firm's own demands with one element per paragraph or table kind, each tagged
``PROTO:<kind>`` in its first paragraph. The writer keeps the page setup,
styles, headers and footers, clones each prototype for its formatting (fonts,
spacing, borders, a box's styled lines), REPLACES every word in it with the
demand's own text, and removes everything else from the body. No word of a
prototype's body ever reaches a demand: the letterhead and the footer legend
are the firm's fixed wording, and the salutation, closing and signature come
from the firm's authored configuration.

SO THE ROUTE (2026-11-25): not a frame. A demand is made from prototypes, and
their text is never used, so the library file keeps every prototype's
formatting and NONE of its words: each prototype's tag stays, every other word
of the body goes, and anything in the body that is not a prototype is dropped,
as the writer drops it. The root writer can then judge the file exactly
(``establish_intake/library_accept.py``): its body holds nothing but tags.
Headers and footers are judged line by line: page numbering, the firm's own
identity, or a line with no case fact (``frame_facts``) is fixed wording and is
kept; any other line refuses the file.

WHAT IT CANNOT DO: tag an untagged letter. Which paragraph of a filed letter is
the firm's "subhead" and which its "body" is a choice a wrong guess would
copy into every demand, so a letter with no ``PROTO:`` tags is refused
(``untagged``); the firm's house reference is the tagged copy of its own letter.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from typing import Iterable

from lxml import etree

from ..firmbase.base import DOCUMENT, W
from . import frame_facts as K, make as make_mod
from .make import Unusable

TAG = re.compile(r"^PROTO:(\w+)$")
_TAG_START = re.compile(r"^\s*PROTO:(\w+)")
_HEAD_FOOT = re.compile(r"^word/(header|footer)\d*\.xml$")
LAYOUTS = ("house", "numbered", "crabtree")


@dataclass(frozen=True)
class DemandMade:
    data: bytes
    fixed_strings: tuple[str, ...]
    kinds: tuple[str, ...]
    counts: dict[str, int] = field(default_factory=dict)
    set_type: str = ""


def layout_kinds(layout: str) -> tuple[str, ...]:
    """The prototype kinds the demand writer of ``layout`` requires."""
    from ..demand import house, house_crabtree, house_numbered

    return {"house": house.KINDS, "numbered": house_numbered.KINDS, "crabtree": house_crabtree.KINDS}[layout]


def _first_paragraph(el: etree._Element) -> etree._Element:
    return el if el.tag == f"{W}p" else next(el.iter(f"{W}p"), el)


def _text(el: etree._Element) -> str:
    return "".join(t.text or "" for t in el.iter(f"{W}t"))


def _keep_tag_only(el: etree._Element, kind: str) -> int:
    """The prototype with its tag as its only words; the count of runs emptied."""
    in_first = set(_first_paragraph(el).iter(f"{W}t"))
    emptied = 0
    tagged = False
    for t in el.iter(f"{W}t"):
        if not tagged and t in in_first:
            t.text, tagged = f"PROTO:{kind}", True
            continue
        if t.text:
            emptied += 1
        t.text = ""
    return emptied


def blank_body(blob: bytes) -> tuple[bytes, list[str], dict[str, int]]:
    """The body as its prototypes alone, each holding only its tag; (bytes,
    the kinds in order, counts)."""
    z = zipfile.ZipFile(io.BytesIO(blob))
    root = etree.fromstring(z.read(DOCUMENT))
    body = root.find(f"{W}body")
    kinds: list[str] = []
    counts = {"prototypes": 0, "words_emptied": 0, "dropped_elements": 0}
    for el in list(body if body is not None else []):
        if el.tag == f"{W}sectPr":
            continue
        m = _TAG_START.match(_text(_first_paragraph(el)))
        if not m or m.group(1) in kinds:
            body.remove(el)  # the writer removes it too: never kept
            counts["dropped_elements"] += 1
            continue
        kinds.append(m.group(1))
        counts["prototypes"] += 1
        counts["words_emptied"] += _keep_tag_only(el, m.group(1))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as w:
        for info in z.infolist():
            raw = z.read(info.filename)
            if info.filename == DOCUMENT:
                raw = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            w.writestr(info, raw)
    return out.getvalue(), kinds, counts


def judge_headers(blob: bytes, identity: list[str]) -> tuple[str, ...]:
    """The header and footer lines kept as the firm's fixed wording. Raises
    ``Unusable`` (``case_words_remain``) when any line holds a case fact."""
    pats = [K.spaced(p) for p in sorted(identity, key=len, reverse=True) if p.strip()]
    allowed = frozenset(w.lower() for p in identity for w in K.WORD.findall(p))
    fixed: list[str] = []
    refused = 0
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for name in z.namelist():
            if not _HEAD_FOOT.match(name):
                continue
            for p in etree.fromstring(z.read(name)).iter(f"{W}p"):
                line = " ".join(_text(p).split())
                if not line or make_mod._PAGE_LINE.fullmatch(line) or make_mod._LEFT_OK.match(line):
                    continue
                if K.case_fact(line, allowed, pats):
                    refused += 1
                else:
                    fixed.append(line)
    if refused:
        raise Unusable("case_words_remain", f"{refused} header or footer line(s)")
    return tuple(dict.fromkeys(fixed))


def make_prototypes(blob: bytes, layout: str, identity: Iterable[str]) -> DemandMade:
    """The demand library file made from the firm's tagged house reference
    ``blob`` for ``layout``. Raises ``Unusable`` with a code."""
    from ..firmbase.adopt import clean_package
    from .frame_make import _with_body
    from .styles_make import _blank_notes_and_people

    if layout not in LAYOUTS:
        raise Unusable("unknown_layout", layout)
    body, kinds, counts = blank_body(blob)
    if not kinds:
        raise Unusable("untagged", "no PROTO:<kind> paragraph; a filed letter must be tagged first")
    missing = [k for k in layout_kinds(layout) if k not in kinds]
    if missing:
        raise Unusable("prototypes_missing", ", ".join(missing))
    made = make_mod.drop_foreign_fields(make_mod.drop_document_links(make_mod.drop_custom_xml(body)))
    made = _with_body(_blank_notes_and_people(clean_package(made)), [])  # also drops the paper's dates and counts
    ids = list(identity)
    fixed = (*judge_headers(made, ids), *(f"PROTO:{k}" for k in kinds))
    return DemandMade(made, tuple(dict.fromkeys(fixed)), tuple(kinds), counts)


__all__ = ["LAYOUTS", "TAG", "DemandMade", "blank_body", "judge_headers", "layout_kinds", "make_prototypes"]
