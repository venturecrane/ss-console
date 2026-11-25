"""Making a discovery library file from one of the firm's own filed papers that
keeps the firm's fixed wording: its styles base (``styles_make``) plus its FRAME
(``drafting/firm_frame.py``), the preliminary statement, general objections,
definitions, signature block and verification, with the case facts taken out.
Pure: bytes in, bytes out. No model call.

WHY (2026-11-24). A styles base cleared the whole body, so the firm's standard
wording never reached a draft: the model wrote it fresh. What makes the paper
the firm's is exactly the wording a styles base threw away.

WHAT IS A CASE FACT, AND WHAT HAPPENS TO IT, in the paragraphs after the
identification block and before the first numbered item, and from the closing
(the Dated line, the signature block, the verification) to the proof of service:

* the parties the identification block names (its whole value and the name
  alone) become ``[[v.responding_party]]`` and the rest (``firm_frame.VALUES``);
* the set number after "Set" becomes ``[[v.set_number]]``;
* the date on a Dated or Executed line becomes ``[[v.date]]``;
* an authored attorney's name becomes ``[[v.signer]]``.

WHAT REFUSES A BLOCK. After those, every capitalised word left must be a word of
a closed list (procedure, the parts of a discovery paper, plain English) or of
the firm's own authored identity; no other date, no amount of money, no email
address or telephone number outside the firm's identity, no case number. A block
with anything else is dropped whole, counted, and never written (fail closed:
a wrong guess about a word must never put a client's name in every later
matter). Only counts and codes come back, never a word of the paper.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from typing import Iterable

from lxml import etree

from ..drafting import firm_frame as F
from .make import Unusable
from .styles_make import make_styles

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
#: The root writer's bounds (``establish_intake/library_accept.py``).
MAX_FIXED_STRINGS, MAX_FIXED_CHARS = 200, 300
_CHUNK = 280

_MONTH = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
_DATE = re.compile(rf"(?i)\b{_MONTH}\s+\d{{1,2}},?\s+\d{{4}}\b|\b\d{{1,2}}/\d{{1,2}}/\d{{2,4}}\b")
#: A closing paragraph whose date is the paper's own: the Dated line, the verification's "Executed on".
_DATED_LINE = re.compile(r"(?i)\b(dated|date:|executed)")
_MONEY = re.compile(r"\$\s*\d")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE = re.compile(r"\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}")
_CASE_NO = re.compile(r"(?i)\bcase\s+(?:no\.?|number)\s*:?\s*\S*\d")
_WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*")

#: The words a firm's standard wording may carry capitalised: procedure, the
#: parts of a discovery paper, and plain English. Closed: a word not here and not
#: in the firm's identity refuses its block.
VOCAB = frozenset(
    """
    a about above according accordingly act action actions additional admission admissions after against all also
    america an and answer answering answers any appear applicable are article as at attorney attorneys based be
    because been before being bound burdensome but by calif california can case civil code codes complaint
    concerning confidential consistent constitution contained contend contention correct counsel county court
    cross-complainant cross-defendant date dated declare declares definition definitions demand describe
    described describing discovery doctrine document documents does each esq esquire evidence except executed
    extent fact facts federal following for foregoing form forth from further furthermore general given has have
    having he hereby herein hereinafter hereto however i identify if in include including information instruction
    instructions interrogatories interrogatory into is it its knowledge law laws limitation llp llc apc pc inc
    matter matters may me meaning means my no nos not nothing notwithstanding objection objections objects of on
    one or other our party parties penalty perjury person persons plaintiff plaintiffs defendant defendants
    pleading possession preliminary privacy privilege privileged procedure produce produced product production
    propounded propounding propounding's proportional provide provided pursuant reasonably record regarding
    relevant request requested requests reserve reserves respectfully respond responded responding response
    responses right rights rule rules said section sections seek seeks served set shall she special state
    statement states subject submitted such superior supplement supplemental that the their them thereto these
    they this those three to true two under undersigned united upon verification verified vague ambiguous
    overbroad oppressive harass harassing was we were what when where whether which who whom will with within
    without work you your two three four five six seven eight nine ten am declare foregoing behalf
    individually entity entities inspection copying custody control diligent search inquiry made
    """.split()
)


@dataclass(frozen=True)
class FrameMade:
    data: bytes
    fixed_strings: tuple[str, ...]
    set_type: str
    counts: dict[str, int] = field(default_factory=dict)


def _fold(text: str) -> str:
    return " ".join((text or "").split())


def _body_lines(blob: bytes) -> list[str]:
    """The source paper's body read as lines, a table row as one line."""
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        body = etree.fromstring(z.read("word/document.xml")).find(f"{_W}body")
    out: list[str] = []
    for el in body if body is not None else []:
        if el.tag == f"{_W}p":
            out.append("".join((t.text or "") if t.tag == f"{_W}t" else "\t" for t in el.iter(f"{_W}t", f"{_W}tab")))
        elif el.tag == f"{_W}tbl":
            out += [
                " | ".join(_fold("".join(t.text or "" for t in tc.iter(f"{_W}t"))) for tc in tr.iter(f"{_W}tc"))
                for tr in el.iter(f"{_W}tr")
            ]
    return out


def _sub(pattern: str, mark: str, text: str) -> str:
    return re.sub(rf"(?i)(?<![\w\[]){pattern}(?![\w\]])", mark, text) if pattern else text


def _blank(text: str, values: dict[str, str], signers: list[str], dated: bool) -> str:
    """One paragraph with its case facts replaced by marked places."""
    for key in ("responding_party", "propounding_party", "responding_name", "propounding_name"):
        if values.get(key):
            text = _sub(re.escape(values[key]), f"[[v.{key}]]", text)
    if values.get("set_number"):
        text = re.sub(rf"(?i)\b(set\s+(?:no\.?\s*)?){re.escape(values['set_number'])}\b", r"\1[[v.set_number]]", text)
    for name in signers:
        text = _sub(re.escape(name), "[[v.signer]]", text)
    return _DATE.sub("[[v.date]]", text) if dated else text


def _residue(text: str, allowed: frozenset[str], identity: list[str]) -> bool:
    """True when the blanked paragraph still holds what may be a case fact."""
    left = F.VALUE.sub(" ", text)
    for piece in identity:
        left = left.replace(piece, " ")
    if _DATE.search(left) or _MONEY.search(left) or _EMAIL.search(left) or _PHONE.search(left):
        return True
    if _CASE_NO.search(left):
        return True
    for w in _WORD.findall(left):
        low = re.sub(r"['’]s$", "", w.lower())
        if w != w.lower() and low not in VOCAB and low not in allowed:
            return True
    return False


def _signers(identity_blocks: Iterable[Iterable[str]]) -> list[str]:
    names = []
    for block in identity_blocks:
        lines = [str(x) for x in block]
        if lines:
            names += [lines[0], re.sub(r"(?i),?\s*esq\.?$", "", lines[0]).strip()]
    return sorted({n for n in names if n}, key=len, reverse=True)


def _is_head(text: str) -> bool:
    words = _WORD.findall(text)
    return bool(words) and text == text.upper() and len(words) <= 10 and not F.VALUE.search(text)


def blank_regions(
    lines: list[str], identity: list[str], blocks: Iterable[Iterable[str]]
) -> tuple[dict[str, list[tuple[str, bool]]], str, dict[str, int]]:
    """(each region's blanked paragraphs, the set type, counts). A region with
    any paragraph that keeps a possible case fact is empty, and counted."""
    where = F.regions(lines)
    if where is None:
        raise Unusable("no_identification_block")
    values, _end = F.identification(lines)
    if not values.get("responding_party"):
        raise Unusable("no_identification_block", "no responding party")
    signers = _signers(blocks)
    allowed = frozenset(w.lower() for p in identity for w in _WORD.findall(p))
    out: dict[str, list[tuple[str, bool]]] = {F.FRONT: [], F.CLOSING: []}
    counts: dict[str, int] = {}
    for region, (a, b) in ((F.FRONT, where.front), (F.CLOSING, where.closing)):
        paras = []
        for raw in lines[a:b]:
            text = _fold(raw.replace("\t", " "))
            if not text:
                continue
            paras.append((_blank(text, values, signers, region == F.CLOSING and bool(_DATED_LINE.search(text))), text))
        refused = sum(1 for blanked, _t in paras if _residue(blanked, allowed, identity))
        counts[f"{region}_paragraphs"] = len(paras)
        counts[f"{region}_refused"] = refused
        if not refused:
            out[region] = [(blanked, _is_head(blanked)) for blanked, _src in paras]
    return out, where.set_type, counts


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
            out.writestr(info, raw)
    return buf.getvalue()


def make_frame(kind: str, blob: bytes, identity: list[str], blocks: Iterable[Iterable[str]] = ()) -> FrameMade:
    """The discovery library file for ``kind`` made from the firm's filed paper
    ``blob``: its styles base with its frame. Raises ``Unusable`` with a code."""
    if kind not in F.KINDS:
        raise Unusable("not_a_discovery_kind", kind)
    blocks = [list(b) for b in blocks]
    regions, set_type, counts = blank_regions(_body_lines(blob), identity, blocks)
    styles = make_styles(kind, blob, identity)
    paragraphs = frame_paragraphs(regions, set_type)
    fixed = list(dict.fromkeys([*styles.fixed_strings, *fixed_of(paragraphs)]))
    if len(fixed) > MAX_FIXED_STRINGS or any(len(s) > MAX_FIXED_CHARS for s in fixed):
        raise Unusable("frame_too_long", f"{len(fixed)} fixed strings")
    counts.update({"fixed_strings": len(fixed), **{f"kept_{r}": len(regions[r]) for r in regions}})
    return FrameMade(_with_body(styles.data, paragraphs), tuple(fixed), set_type, counts)


__all__ = ["VOCAB", "FrameMade", "blank_regions", "fixed_of", "frame_paragraphs", "make_frame"]
