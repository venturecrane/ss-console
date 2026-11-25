"""Making a discovery library file from one of the firm's own filed papers that
keeps the firm's fixed wording: its styles base (``styles_make``) plus its FRAME
(``drafting/firm_frame.py``), the preliminary statement, general objections,
definitions, signature block and verification, with the case facts taken out.
Pure: bytes in, bytes out. No model call.

WHY (2026-11-24). A styles base cleared the whole body, so the firm's standard
wording never reached a draft: the model wrote it fresh. What makes the paper
the firm's is exactly the wording a styles base threw away.

WHAT IS A CASE FACT, AND WHAT HAPPENS TO IT (``frame_facts`` decides), in the
paragraphs after the identification block and before the first numbered item,
and from the closing (the Dated line, the signature block, the verification) to
the proof of service:

* the parties (the identification block and the caption, with their shorter
  forms) become ``[[v.responding_party]]`` and the rest (``firm_frame.VALUES``),
  wherever they appear;
* the set number after "Set" becomes ``[[v.set_number]]``;
* a date in the closing becomes ``[[v.date]]`` (it is the paper's own date);
* an authored attorney's name becomes ``[[v.signer]]``, its spacing ignored.

A PARAGRAPH that still holds a case fact after that (a matter-tailored
definition, a date in the front, a place, a provider) is dropped ALONE and
counted; the rest of its block is kept. In the front it leaves a hole, where a
draft puts its own matter definitions. Fail closed, paragraph by paragraph: a
wrong guess about a word must never put a client's name in every later matter.

THE FOOTER (2026-11-25: 11 of 16 real papers stopped there). A footer line that
names the paper's kind, its running title, keeps only its title words ("Responses
to Special Interrogatories, Set One"): the party names in it go, never a
refusal. Each draft made in the file prints its own title there
(``pleading.retitle_footer``), and the chat tool, which does not retitle, prints
no mark and no name.

Only counts and codes come back, never a word of the paper.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from typing import Iterable

from lxml import etree

from ..drafting import firm_frame as F
from . import frame_facts as K, frame_lines as L
from .make import TITLE_WORDS, Unusable
from .styles_make import make_styles

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
#: The root writer's bounds (``establish_intake/library_accept.py``).
MAX_FIXED_STRINGS, MAX_FIXED_CHARS = 200, 300
_CHUNK = 280
#: A front paragraph dropped for a case fact: a hole the draft fills.
HOLE = ""
VOCAB = K.VOCAB
_FOOTER = re.compile(r"^word/footer\d*\.xml$")
#: A bare year left in the closing (a date's remnant), not a code section ("2015.5").
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b(?![.\d])")
#: Where a kept sentence may end: a full stop, a colon, a closing quote or bracket.
_ENDS = re.compile(r"[.:;!?\"”)\]]\s*$")
_TITLE_TOKEN = re.compile(r"[A-Za-z]+(?:['’][Ss])?")


@dataclass(frozen=True)
class FrameMade:
    data: bytes
    fixed_strings: tuple[str, ...]
    set_type: str
    counts: dict[str, int] = field(default_factory=dict)


def _fold(text: str) -> str:
    return " ".join((text or "").split())


_RUN_TEXT = {f"{_W}tab": "\t", f"{_W}br": "\n", f"{_W}cr": "\n"}


def _body_lines(blob: bytes) -> list[str]:
    """The source paper's body read as lines, a table row as one line. A line
    break inside a paragraph (``w:br``) is kept as a newline: a closing is often
    one paragraph of lines (Dated, the firm, By, the signer, Attorneys for)."""
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        body = etree.fromstring(z.read("word/document.xml")).find(f"{_W}body")
    out: list[str] = []
    for el in body if body is not None else []:
        if el.tag == f"{_W}p":
            parts = el.iter(f"{_W}t", *_RUN_TEXT)
            out.append("".join((t.text or "") if t.tag == f"{_W}t" else _RUN_TEXT[t.tag] for t in parts))
        elif el.tag == f"{_W}tbl":
            out += [
                " | ".join(_fold("".join(t.text or "" for t in tc.iter(f"{_W}t"))) for tc in tr.iter(f"{_W}tc"))
                for tr in el.iter(f"{_W}tr")
            ]
    return out


def _signers(identity_blocks: Iterable[Iterable[str]]) -> list[re.Pattern[str]]:
    """Each authored attorney's name as it may be printed, its spacing ignored."""
    names: set[str] = set()
    for block in identity_blocks:
        lines = [str(x) for x in block]
        if lines:
            names |= {lines[0], re.sub(r"(?i),?\s*esq\.?$", "", lines[0]).strip()}
    return [K.spaced(n) for n in sorted((n for n in names if n), key=len, reverse=True)]


@dataclass(frozen=True)
class Facts:
    """What the paper's own text says its case facts are, and the firm's identity."""

    values: dict[str, str]
    pairs: list[tuple[str, str]]
    signers: list[re.Pattern[str]]
    allowed: frozenset[str]
    identity: list[re.Pattern[str]]
    set_type: str = ""

    def identity_only(self, text: str) -> bool:
        for pat in self.identity:
            text = pat.sub(" ", text)
        return not text.strip(" ,.;:")

    def blank(self, text: str, closing: bool) -> str:
        """One paragraph with its case facts replaced by marked places."""
        if not closing and L.ATTORNEY_LINE.match(text) and not self.identity_only(text):
            return "[[v.signer]]"
        text = K.mark_names(text, self.pairs)
        number = self.values.get("set_number")
        if number:
            text = re.sub(rf"(?i)\b(set\s+(?:no\.?\s*)?){re.escape(number)}\b", r"\1[[v.set_number]]", text)
        for pat in self.signers:
            text = pat.sub("[[v.signer]]", text)
        text = K.mark_set_type(text, self.set_type)
        if not closing:
            return text
        dated = L.DATED.match(text)
        if dated and L.dated_value(dated.group(2)):  # the whole value is the date, a blank and a year included
            return dated.group(1) + " [[v.date]]"
        return K.DATE.sub("[[v.date]]", K.DATE_BLANK.sub("[[v.date]]", text))


def facts_of(lines: list[str], identity: list[str], blocks: Iterable[Iterable[str]]) -> tuple[Facts, F.Regions]:
    where = F.regions(lines)
    if where is None:
        raise Unusable("no_identification_block")
    values = K.party_values(lines, where.id_end)
    if not values.get("responding_party"):
        raise Unusable("no_identification_block", "no responding party")
    allowed = frozenset(w.lower() for p in identity for w in K.WORD.findall(p))
    # A party's one-word short form that is also a word of the firm's own
    # identity is never marked: it would cut the firm's name in the signature.
    pairs = [(t, m) for t, m in K.name_marks(values, K.caption_parties(lines[: where.id_end])) if t not in allowed]
    pats = [K.spaced(p) for p in sorted(identity, key=len, reverse=True) if p.strip()]
    return Facts(values, pairs, _signers(blocks), allowed, pats, where.set_type), where


def _is_head(text: str) -> bool:
    words = K.WORD.findall(text)
    return bool(words) and text == text.upper() and len(words) <= 10 and not F.VALUE.search(text)


def blank_regions(
    lines: list[str], identity: list[str], blocks: Iterable[Iterable[str]]
) -> tuple[dict[str, list[tuple[str, bool]]], str, dict[str, int]]:
    """(each region's blanked paragraphs, the set type, counts). A paragraph
    that still holds a possible case fact is dropped alone and counted; in the
    front it leaves one :data:`HOLE` per run of dropped paragraphs."""
    facts, where = facts_of(lines, identity, blocks)
    out: dict[str, list[tuple[str, bool]]] = {F.FRONT: [], F.CLOSING: []}
    counts: dict[str, int] = {}
    front = [_fold(raw.replace("\t", " ")) for raw in lines[slice(*where.front)]]
    signed = L.signer_lines(L.closing_lines(lines[slice(*where.closing)]), facts.identity_only)
    for region, texts in ((F.FRONT, front), (F.CLOSING, signed)):
        kept, why = _judge(facts, [t for t in texts if t], region == F.CLOSING)
        dropped = sum(why.values())
        counts[f"{region}_paragraphs"] = sum(1 for t, _h in kept if t != HOLE) + dropped
        counts[f"{region}_refused"] = dropped
        counts.update({f"{region}_drop_{k}": v for k, v in why.items()})
        out[region] = kept
    return out, where.set_type, counts


def _judge(facts: Facts, texts: list[str], closing: bool) -> tuple[list[tuple[str, bool]], dict[str, int]]:
    """Each paragraph kept (marked) or dropped alone, with the reason codes of
    the drops. In the front a dropped paragraph leaves one hole per run, a kept
    paragraph whose sentence runs on into a dropped one goes with it (a hole
    never cuts a sentence). A numbered list that loses an item keeps its other
    items: ``firm_frame`` numbers every list afresh when it lays the frame, the
    draft's own definitions in the hole included, so a gap never shows."""
    kept: list[tuple[str, bool]] = []
    why: dict[str, int] = {}
    for text in texts:
        blanked = facts.blank(text, closing)
        reason = "year" if closing and _YEAR.search(F.VALUE.sub(" ", blanked)) else ""
        reason = reason or K.case_fact(blanked, facts.allowed, facts.identity)
        if not reason:
            kept.append((blanked, _is_head(blanked)))
            continue
        why[reason] = why.get(reason, 0) + 1
        if closing:
            continue
        if kept and kept[-1][0] != HOLE and not kept[-1][1] and not _ENDS.search(kept[-1][0]):
            kept.pop()
            why["sentence_runs_on"] = why.get("sentence_runs_on", 0) + 1
        if not kept or kept[-1][0] != HOLE:
            kept.append((HOLE, False))
    return kept, why


def _title_words(text: str) -> str:
    keep = [w for w in _TITLE_TOKEN.findall(text) if re.sub(r"['’]s$", "", w.lower()) in TITLE_WORDS]
    return " ".join(keep)


def retitled_footers(blob: bytes) -> tuple[bytes, int]:
    """The source with each footer running title kept as its title words only
    (the module docstring); (bytes, how many lines)."""
    from ..drafting.pleading import RUNNING_TITLE

    buf, n = io.BytesIO(), 0
    with zipfile.ZipFile(io.BytesIO(blob)) as src, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for info in src.infolist():
            raw = src.read(info.filename)
            if _FOOTER.match(info.filename):
                root = etree.fromstring(raw)
                for p in root.iter(f"{_W}p"):
                    ts = list(p.iter(f"{_W}t"))
                    text = "".join(t.text or "" for t in ts)
                    if not ts or not RUNNING_TITLE.search(text) or _title_words(text) == _fold(text):
                        continue
                    ts[0].text = _title_words(text)
                    for t in ts[1:]:
                        t.text = ""
                    n += 1
                raw = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            out.writestr(info, raw)
    return buf.getvalue(), n


def _chunks(text: str) -> list[str]:
    """``text`` cut between words into pieces of at most ``_CHUNK`` characters,
    its marked places never cut, joined back with nothing between them."""
    out, cur = [], ""
    for tok in re.findall(r"\[\[[a-z0-9._]+\]\]|\S+\s*|\s+", text):
        if cur and len(cur) + len(tok) > _CHUNK:
            out.append(cur)
            cur = ""
        cur += tok
    return [*out, cur] if cur else out


def frame_paragraphs(blocks: dict[str, list[tuple[str, bool]]], set_type: str) -> list[str]:
    """The body paragraphs that carry a frame (``firm_frame``'s format)."""
    out = [f"[[frame.set_type.{set_type}]]"] if set_type else []
    for region in (F.FRONT, F.CLOSING):
        for n, (text, head) in enumerate(blocks[region], start=1):
            if text == HOLE:
                out.append(f"[[frame.{region}.{n}.hole]]")
                continue
            lead = f"[[frame.{region}.{n}{'.head' if head else ''}]]"
            out += [lead + c for c in _chunks(text)]
    return out


def fixed_of(paragraphs: Iterable[str]) -> list[str]:
    """The fixed strings the root writer's check must be told: every run of
    words between marked places."""
    seen: dict[str, None] = {}
    for p in paragraphs:
        for seg in re.split(r"\[\[[a-z0-9._]+\]\]", p):
            folded = _fold(seg)
            if folded and re.search(r"[^\W_]", folded):
                seen.setdefault(folded, None)
    return list(seen)


def _with_body(blob: bytes, paragraphs: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(blob)) as src, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for info in src.infolist():
            raw = src.read(info.filename)
            if info.filename == "word/document.xml":
                root = etree.fromstring(raw)
                body = root.find(f"{_W}body")
                sect = body.find(f"{_W}sectPr") if body is not None else None
                for text in paragraphs:
                    p = etree.Element(f"{_W}p")
                    t = etree.SubElement(etree.SubElement(p, f"{_W}r"), f"{_W}t")
                    t.text, t.attrib[_XML_SPACE] = text, "preserve"
                    if sect is not None:
                        sect.addprevious(p)
                    elif body is not None:
                        body.append(p)
                raw = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            elif info.filename in (_CORE, _APP):
                raw = _without_dates(raw)
            out.writestr(info, raw)
    return buf.getvalue()


_CORE, _APP = "docProps/core.xml", "docProps/app.xml"
#: What dates the firm's paper, counts it or names where it came from: created,
#: saved, printed, revision, editing time, its template, and its page, word,
#: character, line and paragraph counts. The made file is no copy of that paper.
_DATED_PROPS = frozenset(
    {
        "created",
        "modified",
        "lastPrinted",
        "revision",
        "TotalTime",
        "Template",
        "Pages",
        "Words",
        "Characters",
        "CharactersWithSpaces",
        "Lines",
        "Paragraphs",
    }
)


def _without_dates(raw: bytes) -> bytes:
    root = etree.fromstring(raw)
    for el in list(root):
        if etree.QName(el).localname in _DATED_PROPS:
            root.remove(el)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def make_frame(kind: str, blob: bytes, identity: list[str], blocks: Iterable[Iterable[str]] = ()) -> FrameMade:
    """The discovery library file for ``kind`` made from the firm's filed paper
    ``blob``: its styles base with its frame. Raises ``Unusable`` with a code."""
    if kind not in F.KINDS:
        raise Unusable("not_a_discovery_kind", kind)
    blocks = [list(b) for b in blocks]
    regions, set_type, counts = blank_regions(_body_lines(blob), identity, blocks)
    source, counts["footer_titles"] = retitled_footers(blob)
    styles = make_styles(kind, source, identity)
    paragraphs = frame_paragraphs(regions, set_type)
    fixed = list(dict.fromkeys([*styles.fixed_strings, *fixed_of(paragraphs)]))
    if len(fixed) > MAX_FIXED_STRINGS or any(len(s) > MAX_FIXED_CHARS for s in fixed):
        raise Unusable("frame_too_long", f"{len(fixed)} fixed strings")
    kept = {f"kept_{r}": sum(1 for t, _h in regions[r] if t != HOLE) for r in regions}
    counts.update({"fixed_strings": len(fixed), **kept})
    return FrameMade(_with_body(styles.data, paragraphs), tuple(fixed), set_type, counts)


__all__ = [
    "HOLE",
    "VOCAB",
    "FrameMade",
    "blank_regions",
    "facts_of",
    "fixed_of",
    "frame_paragraphs",
    "make_frame",
    "retitled_footers",
]
