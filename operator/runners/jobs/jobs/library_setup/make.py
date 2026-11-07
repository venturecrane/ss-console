"""Making the firm's library file from the document the administrator chose, and
proving it carries none of that document's case. Pure: bytes in, bytes out.

THE STEPS, IN ORDER, EACH FAIL CLOSED:

1. The kind's plan adopts and cleans it (``drafting/brief_base.adopt``,
   ``drafting/memo_base.adopt``): every word of the body becomes a marked place,
   and the properties, titles and thumbnail that name a person or a matter go.
2. Its custom XML parts go, with every pointer to them (``drop_custom_xml``). The
   clean keeps them, and they can hold a document system's metadata about the
   matter; the no-client-text check refuses any. So do its links to a document
   system or a template (``drop_document_links``: the attached template and its
   external relationship, document variables, mail merge settings) and every
   header or footer field that is not page numbering (``drop_foreign_fields``: a
   document system's footer stamp is a field whose cached result is its id, and
   the root writer refuses any field it cannot name). None of these carries the
   look. Found on the firm's own brief in the A&P rehearsal of 2026-11-07, where
   the accept job's own checks passed and the root writer's check refused all
   three.
3. What its headers, footers, notes and comments still say is judged against an
   allow list (``judge_stories``): a line may remain only when it is page
   numbering, or nothing but the firm's own authored identity (its name, street,
   city, telephone, fax, website, from ``customer.yaml`` ``firm_identity``, and
   the lines of the attorney blocks the firm authored in its drafting inputs)
   and closed lists of label words and of running-title words (a pleading
   footer's "Plaintiff's Mediation Brief"). Anything else (a party's name, a matter
   number, a date) refuses the file. A note or a comment may hold no words at all.
4. The marked places must be readable for writing (``firmbase.markers``, fail
   closed since #3857) and the file must load as a firm base.

THE ANSWER names what remained by category and count, never the words: page
numbering lines, firm identity lines, and (on a refusal) the lines refused. The
identity lines that remained travel with the acceptance as its fixed strings,
so the root writer's own no-client-text check (``establish_intake/blank_form``)
passes them and nothing else.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any, Iterable

from lxml import etree

from ..firmbase import FirmBase, FirmBaseUnfit, Source
from ..firmbase.base import DOCUMENT, W
from ..firmbase.markers import unwritable

_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_CT = "{http://schemas.openxmlformats.org/package/2006/content-types}"
CONTENT_TYPES = "[Content_Types].xml"
SETTINGS = "word/settings.xml"
SETTINGS_RELS = "word/_rels/settings.xml.rels"
#: Settings that link the file to a template or a document system; each can carry a path, a name or an id.
_LINK_SETTINGS = frozenset({"attachedTemplate", "docVars", "mailMerge"})
#: The fields a header or footer may keep: page numbering, as the root writer's check allows them.
FIELDS_ALLOWED = frozenset({"PAGE", "NUMPAGES", "SECTIONPAGES", "SECTION"})
_PAGE_LINE = re.compile(r"(?i)(\d+|page|of|\s)*")
_HEAD_FOOT = re.compile(r"^word/(header|footer)\d*\.xml$")
_NOTES = re.compile(r"^word/(footnotes|endnotes|comments)\d*\.xml$")
#: Words a letterhead line may carry beside the firm's own values.
LABEL_WORDS = ("telephone", "tel", "phone", "facsimile", "fax", "email", "e-mail", "website", "web", "www")
#: The words a document's running title may carry that name its kind and side,
#: never a case: "Plaintiff's Mediation Brief" in a pleading footer.
TITLE_WORDS = (
    "plaintiff",
    "plaintiffs",
    "defendant",
    "defendants",
    "mediation",
    "settlement",
    "conference",
    "brief",
    "statement",
    "memorandum",
    "memo",
    "confidential",
    "privileged",
    "attorney",
    "work",
    "product",
    "and",
)
_LEFT_OK = re.compile(r"\A[\s\W_]*\Z")


class Unusable(ValueError):
    """The chosen document cannot be made into a library file; ``code`` says why."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True)
class Made:
    data: bytes
    fixed_strings: tuple[str, ...]
    residue: dict[str, int] = field(default_factory=dict)


def _fold(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _serial(root: Any) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def drop_custom_xml(blob: bytes) -> bytes:
    """``blob`` without its custom XML parts and every relationship and content
    type that names them."""
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(blob)) as src, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for info in src.infolist():
            name = info.filename
            if name.startswith("customXml/"):
                continue
            raw = src.read(name)
            if name.endswith(".rels"):
                root = etree.fromstring(raw)
                for rel in list(root):
                    if "customXml" in rel.get("Target", "") or rel.get("Type", "").endswith("/customXml"):
                        root.remove(rel)
                raw = _serial(root)
            elif name == CONTENT_TYPES:
                root = etree.fromstring(raw)
                for el in list(root):
                    if el.tag == f"{_CT}Override" and el.get("PartName", "").startswith("/customXml/"):
                        root.remove(el)
                raw = _serial(root)
            out.writestr(info, raw)
    return buf.getvalue()


def drop_document_links(blob: bytes) -> bytes:
    """``blob`` without its links to a template or a document system: the attached
    template and its external relationship, document variables and mail merge
    settings. None carries the look; each can carry a path, a name or an id."""
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(blob)) as src, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for info in src.infolist():
            raw = src.read(info.filename)
            if info.filename == SETTINGS:
                root = etree.fromstring(raw)
                for el in list(root):
                    if etree.QName(el).localname in _LINK_SETTINGS:
                        root.remove(el)
                raw = _serial(root)
            elif info.filename == SETTINGS_RELS:
                root = etree.fromstring(raw)
                for rel in list(root):
                    if rel.get("TargetMode") == "External" or rel.get("Type", "").endswith("/attachedTemplate"):
                        root.remove(rel)
                raw = _serial(root)
            out.writestr(info, raw)
    return buf.getvalue()


def _complex_fields(story: Any) -> list[tuple[str, list[Any]]]:
    """Each complex field of the story, outermost only: (the first word of its
    instruction, upper-cased; the runs from its begin to its end, its cached
    result among them)."""
    out: list[tuple[str, list[Any]]] = []
    open_fields: list[tuple[list[str], list[Any]]] = []
    for run in list(story.iter(f"{W}r")):
        kinds = [c.get(f"{W}fldCharType") for c in run.iter(f"{W}fldChar")]
        if "begin" in kinds:
            open_fields.append(([], []))
        if open_fields:
            open_fields[-1][1].append(run)
            open_fields[-1][0].extend(t.text or "" for t in run.iter(f"{W}instrText"))
        if "end" in kinds and open_fields:
            code, runs = open_fields.pop()
            if open_fields:
                open_fields[-1][1].extend(runs)
            else:
                out.append((("".join(code).split() or [""])[0].upper(), runs))
    return out


def drop_foreign_fields(blob: bytes) -> bytes:
    """``blob`` with every header and footer field that is not page numbering
    removed, its cached result with it. A document system's footer stamp is such a
    field, and the root writer's check refuses any field it cannot name. Fields in
    the body are the plan's business."""
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(blob)) as src, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for info in src.infolist():
            raw = src.read(info.filename)
            if _HEAD_FOOT.match(info.filename):
                root = etree.fromstring(raw)
                for word, runs in _complex_fields(root):
                    if word not in FIELDS_ALLOWED:
                        for run in runs:
                            if run.getparent() is not None:
                                run.getparent().remove(run)
                for simple in list(root.iter(f"{W}fldSimple")):
                    if ((simple.get(f"{W}instr") or "").split() or [""])[0].upper() not in FIELDS_ALLOWED:
                        simple.getparent().remove(simple)
                raw = _serial(root)
            out.writestr(info, raw)
    return buf.getvalue()


def _paragraph_texts(root: Any) -> list[str]:
    return ["".join(t.text or "" for t in p.iter(f"{W}t")) for p in root.iter(f"{W}p")]


def _allowed(pieces: Iterable[str]) -> list[str]:
    """The allow list's pieces, case folded, longest first."""
    folded = {_fold(p).casefold() for p in pieces if _fold(p)}
    return sorted(folded, key=len, reverse=True)


def identity_only(text: str, pieces: list[str]) -> bool:
    """The line is nothing but the firm's own values and label words."""
    left = _fold(text).casefold()
    for piece in pieces:
        left = left.replace(piece, " ")
    for word in (*LABEL_WORDS, *TITLE_WORDS):
        left = re.sub(rf"(?<![\w-]){re.escape(word)}(?:'s|s'|\u2019s)?(?![\w-])", " ", left)
    return bool(_LEFT_OK.match(left)) and not re.search(r"\d", left)


def judge_stories(blob: bytes, pieces: Iterable[str]) -> tuple[tuple[str, ...], dict[str, int]]:
    """(the identity lines that remain, the residue by category). Raises
    ``Unusable`` with the counts when any line is not allowed."""
    allowed = _allowed(pieces)
    residue = {"page_numbering": 0, "firm_identity": 0, "refused_words": 0, "refused_digits": 0, "notes_words": 0}
    fixed: list[str] = []
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for name in z.namelist():
            notes = bool(_NOTES.match(name))
            if not (notes or _HEAD_FOOT.match(name)):
                continue
            for text in _paragraph_texts(etree.fromstring(z.read(name))):
                line = _fold(text)
                if not line or _LEFT_OK.match(line):
                    continue
                if _PAGE_LINE.fullmatch(line) and not notes:
                    residue["page_numbering"] += 1
                elif notes:
                    residue["notes_words"] += 1
                elif identity_only(line, allowed):
                    residue["firm_identity"] += 1
                    fixed.append(line)
                else:
                    residue["refused_digits" if re.search(r"\d", line) else "refused_words"] += 1
    refused = residue["refused_words"] + residue["refused_digits"] + residue["notes_words"]
    if refused:
        raise Unusable("case_words_remain", ", ".join(f"{k} {v}" for k, v in residue.items() if v))
    return tuple(dict.fromkeys(fixed)), residue


def _markers_ok(kind: str, blob: bytes, source: Source) -> None:
    body = etree.fromstring(zipfile.ZipFile(io.BytesIO(blob)).read(DOCUMENT)).find(f"{W}body")
    refused = unwritable(body)
    if refused:
        raise Unusable("marked_places_unwritable", f"{len(refused)} line(s)")
    try:
        FirmBase.load(kind, source, blob)
    except FirmBaseUnfit as exc:
        raise Unusable("not_a_firm_base", str(exc)) from exc


def _adopter(kind: str) -> Any:
    from ..drafting import brief_base, memo_base

    return {"mediation_brief": brief_base.adopt, "memo": memo_base.adopt}[kind]


def make(kind: str, blob: bytes, source: Source, identity: Iterable[str]) -> Made:
    """The library file for ``kind`` made from ``blob`` (the module docstring)."""
    from ..drafting.outcome import DraftingFailed

    try:
        adopted, _notes = _adopter(kind)(blob, source)
    except (DraftingFailed, FirmBaseUnfit, KeyError, ValueError, zipfile.BadZipFile, etree.XMLSyntaxError) as exc:
        raise Unusable("plan_refused", type(exc).__name__) from exc
    made = drop_foreign_fields(drop_document_links(drop_custom_xml(adopted)))
    fixed, residue = judge_stories(made, identity)
    _markers_ok(kind, made, source)
    return Made(made, fixed, residue)


def identity_pieces(firm_identity: Any, attorney_blocks: Iterable[Iterable[str]]) -> list[str]:
    """The allow list: the firm's authored identity values and the lines of its
    authored attorney blocks."""
    out = [
        getattr(firm_identity, f, None) or "" for f in ("name", "street", "city_state_zip", "phone", "fax", "website")
    ]
    for block in attorney_blocks:
        out.extend(str(line) for line in block)
    return [p for p in out if _fold(p)]


__all__ = [
    "LABEL_WORDS",
    "TITLE_WORDS",
    "Made",
    "Unusable",
    "drop_custom_xml",
    "identity_only",
    "identity_pieces",
    "judge_stories",
    "make",
]
