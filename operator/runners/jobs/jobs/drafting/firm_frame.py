"""A discovery paper's FRAME in the firm's own words: what comes between its
identification block and its first numbered item (the preliminary statement,
the general objections, a set's definitions and instructions), and what comes
after its last item (the Dated line, the signature block, the verification).

WHY (2026-11-24). A firm's discovery responses read the same in every matter
outside their answers: the same preliminary statement, the same general
objections, the same verification. A styles file gave the draft the firm's
fonts and paper only, and the model wrote those blocks fresh in its own words.
Now the library file SMD stocks from one of the firm's own filed papers
(``library_setup/frame_make.py``) carries the frame, blanked: each paragraph is
the firm's fixed words with marked places where its case facts were. This
module reads that frame back and puts it into a draft, with code filling the
marked places from the draft's own identification block. The numbered items
and their answers stay the draft's.

THE FORMAT, in the styles file's body (the renderer clears the body before it
writes, so these paragraphs never print themselves):

* ``[[frame.set_type.<set type>]]``: the kind of set the source paper was;
* ``[[frame.<front|closing>.<n>]]`` or ``[[frame.<front|closing>.<n>.head]]``
  starts each paragraph; a long paragraph is several body paragraphs with the
  same lead mark, joined in order with nothing between them (each body
  paragraph keeps its own spaces), so no fixed string passes the root writer's
  limit of 300 characters;
* value marks: ``[[v.responding_party]]`` and ``[[v.propounding_party]]`` (the
  identification block's whole value, "Plaintiff JANE DOE"),
  ``[[v.responding_name]]`` and ``[[v.propounding_name]]`` (the name alone),
  ``[[v.set_number]]``, ``[[v.date]]`` (the Dated or Executed line),
  ``[[v.signer]]`` (the attorney who signs).

A block whose marked places the draft cannot fill is left as the draft wrote it,
and the attorney's notes say so. No model call.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any

from .revise.responses import plain
from .revise.responses import _head as item_head

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
KINDS = ("discovery_response", "discovery_set")
FRONT, CLOSING = "front", "closing"
LEAD = re.compile(r"^\[\[frame\.(front|closing)\.(\d+)(\.head|\.hole)?\]\]")
#: A paragraph of the firm's paper that held a case fact: dropped, its place kept.
HOLE = ""
FRONT_NAME = "opening (preliminary statement, objections, definitions)"
#: A frame with no closing (every line held a case fact) is still a frame: the
#: draft keeps its own closing, the signature block the job renders from the
#: firm's attorney roster, and the record says ``closing_from: draft``.
CLOSING_NAME = "closing (signature, verification)"
SET_TYPE = re.compile(r"^\[\[frame\.set_type\.([a-z_]+)\]\]\s*$")
VALUE = re.compile(r"\[\[v\.([a-z_]+)\]\]")
#: The kind-of-set places (``library_setup/frame_facts.type_values``): filled
#: with the draft's own kind, so a frame serves every kind of set.
TYPE_VALUES = (
    "set_name",
    "set_name_upper",
    "item",
    "item_cap",
    "item_upper",
    "items",
    "items_cap",
    "items_upper",
    "code_chapter",
)
VALUES = (
    "responding_party",
    "propounding_party",
    "responding_name",
    "propounding_name",
    "set_number",
    "date",
    "signer",
    *TYPE_VALUES,
)
#: The identification block's lines: "PROPOUNDING PARTY: ...", "SET NO.: ONE".
#: Anchored at the line's start, a party line with its colon: prose that names
#: "the responding party" is never an identification line.
ID_LINE = re.compile(
    r"(?i)^[\s|]*(?:(PROPOUNDING|RESPONDING)\s+PARTY\s*:|SET\s+(?:NO\.?|NUMBER)\s*:?)\s*\|?\s*([^|\n]*)"
)
#: A line naming when the set was served: part of the identification block.
SERVED_LINE = re.compile(r"(?i)^\W*(?:date\s+(?:of\s+)?served|served\s+on|date\s+of\s+service)\s*:")
#: Where a paper's closing starts (``revise/responses.py`` ``_CLOSING`` without service).
CLOSING_START = re.compile(r"^(DATED|DATE:|RESPECTFULLY|VERIFICATION)\b", re.I)
#: Where it ends: the proof of service and a set's declaration for additional
#: discovery are the job's own attachments (``render._declare`` fills the firm's
#: authored declaration form with this set's count), so neither is frame: the
#: firm's filed declaration tells its own case (2026-11-25, real papers).
SERVICE = re.compile(
    r"^(PROOF OF SERVICE|DECLARATION OF SERVICE|DECLARATION FOR ADDITIONAL DISCOVERY|DECLARATION\b.{0,80}?20(?:30|33)\.050)",
    re.I,
)
_ROLE = re.compile(
    r"(?i)^(?:\s*(?:plaintiffs?|defendants?|cross[- ](?:complainants?|defendants?)|petitioners?|respondents?)"
    r"\s*(?:and|,)?\s*)+"
)


def name_of(value: str) -> str:
    """The party's name alone: an identification value without its role words."""
    return _ROLE.sub("", value or "").strip(" ,;:")


def identification(lines: list[str]) -> tuple[dict[str, str], int]:
    """(the identification block's values, the index of its last line, or -1)."""
    out: dict[str, str] = {}
    last = -1
    for i, line in enumerate(lines):
        m = ID_LINE.search(plain(line))
        if not m:
            continue
        value = " ".join(m.group(2).split()).strip(" *_")
        if not value:
            continue
        key = f"{m.group(1).lower()}_party" if m.group(1) else "set_number"
        out.setdefault(key, value)
        last = i
    # A served-date line right after the block is the block's (2026-11-25: a
    # response's front refused its "Date Served:" line as a case fact).
    for j in range(last + 1, min(last + 4, len(lines))) if last >= 0 else ():
        if SERVED_LINE.match(plain(lines[j])):
            last = j
        elif plain(lines[j]):
            break
    for role in ("responding", "propounding"):
        if f"{role}_party" in out:
            out[f"{role}_name"] = name_of(out[f"{role}_party"])
    return out, last


@dataclass(frozen=True)
class Regions:
    """Where a paper's frame sits, by line index: ``front`` [start, end) and
    ``closing`` [start, end); an empty range when the paper has none."""

    id_end: int
    front: tuple[int, int]
    closing: tuple[int, int]
    set_type: str = ""


def regions(lines: list[str]) -> Regions | None:
    """The frame's places in a paper read as lines; None when it has no
    identification block or no numbered item."""
    _values, id_end = identification(lines)
    if id_end < 0:
        return None
    heads = [(i, h) for i, ln in enumerate(lines) if i > id_end for h in [item_head(plain(ln))] if h is not None]
    if not heads:
        return None
    first, last = heads[0][0], heads[-1][0]
    start = next((i for i in range(last + 1, len(lines)) if CLOSING_START.match(plain(lines[i]))), len(lines))
    end = next((i for i in range(last + 1, len(lines)) if SERVICE.match(plain(lines[i]))), len(lines))
    start = min(start, end)  # a Dated line inside an attachment is not the paper's closing
    return Regions(id_end, (id_end + 1, first), (start, end), heads[0][1][1])


@dataclass
class Frame:
    set_type: str = ""
    blocks: dict[str, list[tuple[str, bool]]] = field(default_factory=lambda: {FRONT: [], CLOSING: []})

    def marks(self, region: str) -> set[str]:
        return {m for text, _h in self.blocks[region] for m in VALUE.findall(text)}


def _body_texts(blob: bytes) -> list[str]:
    from lxml import etree

    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        body = etree.fromstring(z.read("word/document.xml")).find(f"{_W}body")
    if body is None:
        return []
    return ["".join(t.text or "" for t in p.iter(f"{_W}t")) for p in body.findall(f"{_W}p")]


def read(blob: bytes | None) -> Frame | None:
    """The frame a library file carries, or None when it carries none."""
    if not blob:
        return None
    try:
        texts = _body_texts(blob)
    except (KeyError, ValueError, zipfile.BadZipFile):
        return None
    frame, seen = Frame(), False
    #: (region, n) -> (its text pieces, its lead's suffix: "", ".head" or ".hole")
    joined: dict[tuple[str, int], tuple[list[str], str]] = {}
    for text in texts:
        st = SET_TYPE.match(text)
        if st:
            frame.set_type, seen = st.group(1), True
            continue
        m = LEAD.match(text)
        if not m:
            continue
        seen = True
        key = (m.group(1), int(m.group(2)))
        joined.setdefault(key, ([], m.group(3) or ""))[0].append(text[m.end() :])
    for (region, _n), (parts, how) in sorted(joined.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        frame.blocks[region].append((HOLE if how == ".hole" else "".join(parts).strip(), how == ".head"))
    return frame if seen and any(frame.blocks.values()) else None


#: A paragraph that defines a term: ``"INCIDENT" means ...``, ``ACCIDENT shall mean ...``.
_DEFINES = re.compile(
    r"^\W*(?:\d{1,3}[.)]\s*)?[\"“]?([A-Z][A-Z0-9 '’-]{1,40}?)[\"”]?\W*\s+"
    r"(?:means|shall\s+mean|refers?\s+to|includes?|is\s+defined)\b"
)
#: A numbered item's number: "25. ", "3) ".
_NUMBERED = re.compile(r"^(\d{1,3})([.)])(\s+)")


def renumber(paras: list[str]) -> list[str]:
    """Each run of numbered paragraphs numbered afresh from its first number: a
    dropped item, or a draft's own definition in a hole, never leaves a gap or
    a repeat (2026-11-25: a set's definitions came through with gaps)."""
    out: list[str] = []
    nxt: int | None = None
    for p in paras:
        m = _NUMBERED.match(p)
        if not m:
            nxt = None
            out.append(p)
            continue
        nxt = int(m.group(1)) if nxt is None else nxt
        out.append(f"{nxt}{m.group(2)}{m.group(3)}{p[m.end() :]}")
        nxt += 1
    return out


def _term(text: str) -> str:
    m = _DEFINES.match(plain(text))
    return " ".join(m.group(1).split()).upper() if m else ""


def _draft_definitions(lines: list[str], frame_texts: list[str]) -> list[str]:
    """The draft's own definitions of terms the frame does not define: the
    matter's facts, written fresh, which go where the frame has a hole."""
    have = {_term(t) for t in frame_texts} - {""}
    paras = [p.strip() for p in "\n".join(lines).split("\n\n") if p.strip()]
    return [p for p in paras if _term(p) and _term(p) not in have]


def _md(paras: list[tuple[str, bool]], values: dict[str, str], fill: list[str] | None = None) -> list[str]:
    """The block as markdown; the first hole takes ``fill`` (the draft's own
    matter definitions), and with no hole they follow the block's last line."""
    out: list[str] = []
    pending = list(fill or [])
    for text, head in paras:
        if text == HOLE:
            out += pending
            pending = []
            continue
        filled = VALUE.sub(lambda m: values[m.group(1)], text)
        out.append(f"**{filled}**" if head else filled)
    return [x for p in renumber(out + pending) for x in (p, "")]


def apply(doc: str, frame: Frame, values: dict[str, str], variant: str | None = None) -> tuple[str, list[str]]:
    """``doc`` with its front and closing in the frame's words, each block only
    when every marked place in it has a value. Returns (doc, notes).

    A frame made from one kind of set serves another of the same paper (a
    response's frame any response, a set's any set): the words that named its
    kind ("Special Interrogatories", "each interrogatory", section 2030.x) are
    marked places, filled with this draft's own kind."""
    from ..library_setup.frame_facts import type_values

    lines = doc.splitlines()
    where = regions(lines)
    if where is None:
        return doc, ["the firm's standard wording was not used: the draft has no identification block or no items"]
    own = variant or ""
    kind = own if type_values(own) else (where.set_type or frame.set_type)
    values = {**type_values(kind), **values}
    notes: list[str] = []
    if frame.set_type and kind and kind != frame.set_type:
        notes.append(f"the firm's standard wording is from its {frame.set_type} paper, worded for this {kind} set")
    out = list(lines)
    named = {FRONT: FRONT_NAME, CLOSING: CLOSING_NAME}
    for region, (a, b) in ((CLOSING, where.closing), (FRONT, where.front)):  # the later block first: indices hold
        if not frame.blocks[region]:
            continue
        missing = sorted(m for m in frame.marks(region) if not values.get(m))
        if missing:
            notes.append(f"the firm's standard {named[region]} was not used: the draft has no {', '.join(missing)}")
            continue
        if region == CLOSING and a >= len(lines):
            notes.append(f"the firm's standard {named[region]} was not used: the draft has no closing")
            continue
        texts = [t for t, _h in frame.blocks[region] if t != HOLE]
        if not texts:
            continue
        fill = _draft_definitions(lines[a:b], texts) if region == FRONT else None
        out[a:b] = ["", *_md(frame.blocks[region], values, fill)]
        notes.append(f"the {named[region]} is the firm's own standard wording, from its library file")
    return "\n".join(out).rstrip() + "\n", notes


def draft_values(doc: str, date: str, signer: str) -> dict[str, str]:
    """The values a draft fills the marked places with: its identification
    block, the draft's date and the signing attorney."""
    found, _end = identification(doc.splitlines())
    return {**found, "date": date, "signer": signer}


def _record(data: Any, frame_from: str, used_for: str, closing_from: str) -> None:
    """The library-resolution record says which kind of set's frame served
    which kinds, and whether the closing was the frame's or the draft's own."""
    import json
    from pathlib import Path

    from operator_connector_sdk.library.caller import FRAME, RECORD_NAME, add_to_record

    try:
        had = json.loads((Path(data) / RECORD_NAME).read_text(encoding="utf-8")).get(FRAME) or {}
    except (OSError, ValueError, AttributeError):
        had = {}
    kinds = sorted({*had.get("used_for", []), *([used_for] if used_for else [])})
    add_to_record(data, FRAME, {"frame_from": frame_from, "used_for": kinds, "closing_from": closing_from})


def at_gate(run: Any, unit: Any, md: str) -> str:
    """A discovery unit's markdown with the firm's frame, when the file the
    document is made in carries one; else as given."""
    if run.cls not in KINDS:
        return md
    from . import library_pick, render

    frame = read(library_pick.pick_base(run).styles)
    if frame is None:
        return md
    body, notes = render.split_notes(md)
    lines = run._block_lines()
    body, said = apply(body, frame, draft_values(body, run._long_date(), lines[0] if lines else ""), unit.variant)
    closing_from = "frame" if any(n.startswith(f"the {CLOSING_NAME} is") for n in said) else "draft"
    _record(run.data, frame.set_type, unit.variant or "", closing_from)
    notes = "\n".join([notes, *(f"- {n}" for n in said)]).strip()
    return run._join(body, notes)


__all__ = ["CLOSING", "FRONT", "KINDS", "VALUES", "Frame", "apply", "at_gate", "draft_values", "read", "regions"]
