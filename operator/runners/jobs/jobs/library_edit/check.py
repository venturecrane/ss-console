"""Is the firm's own save of its accepted library file still a form we can write
into, with nothing of a case in it? A check on bytes, in code (design
2026-11-06, piece 4).

WHAT A FIRM DOES. It opens its library file in Word, changes the look (a
margin, a font, a style), and saves. The case system keeps the save as a new
version of the same file. Word writes the saver's name into the package's
properties and a link to the template it was opened with into its settings;
neither is the look, and both fail the root writer's no-client-text check.

THE STEPS, EACH FAIL CLOSED, EACH A CODE:

1. :func:`clean`: the properties a person's save fills (``firmbase.adopt.
   clean_package``, the forms path's own cleaning), custom XML, the document
   links and template link (``library_setup.make.drop_document_links``) and any
   header or footer field that is not page numbering
   (``make.drop_foreign_fields``). Nothing a firm sees is touched: the body,
   the styles, the headers and footers' words, the page setup.
2. :func:`problems` on the cleaned bytes, against the slot's accepted copy (the
   seat-kept one, by fingerprint):

   * ``words_changed``: a paragraph whose text is not a marked place,
     punctuation, page numbering in a header or footer, or one of the slot's
     fixed strings. The fixed strings are read off the accepted copy itself
     (:func:`fixed_of`): every word the root writer passed in it, and nothing
     else. A firm that changes a fixed word, or types a word anywhere, is
     refused, and the draft is made in the accepted copy;
   * ``tracked_changes``, ``comments``: the root writer refuses both, and a
     tracked deletion carries words;
   * ``field_added``: a field that is not page numbering anywhere in the file;
   * ``body_picture_added``: more pictures in the body than the accepted copy
     has (the root writer cannot read a picture);
   * a ``marked`` file: ``marked_places_changed`` (not the accepted copy's
     marked places), ``marked_places_unwritable`` (``firmbase.markers``, fail
     closed since #3857), ``not_a_firm_base``;
   * a ``styles`` file: ``not_one_section``, ``page_setup_missing``,
     ``named_style_removed`` (a style the accepted copy defines and the
     renderer reads);
   * a ``prototypes`` file: ``contract_not_checkable`` (the root writer refuses
     every prototypes file; a demand reference is accepted by a later piece).

THE TEXT RULE is the root writer's (``establish_intake/blank_form.py``): a
paragraph's whole text, or each piece of it between marked places, read with
its whitespace folded. The root writer runs its own check again on the same
bytes before anything is written to the seat's store; this one runs first, in
the job, because the job makes its draft in the bytes before the root writer
answers.

Problems are codes and counts, never the words found.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Iterable, Iterator
from typing import Any

from lxml import etree

from ..firmbase.base import DOCUMENT, W

MARKED, STYLES, PROTOTYPES = "marked", "styles", "prototypes"
_MARK = re.compile(r"\[\[[a-z0-9._]+\]\]|\{\{[a-z][a-z0-9._]*\}\}")
_PAGE_LINE = re.compile(r"(?i)(\d+|page|of|\s)*")
_PUNCT_ONLY = re.compile(r"\A[\s\W_]*\Z")
_STORY_WITH_PAGES = re.compile(r"\Aword/(header|footer)\d*\.xml\Z")
_FIELDS_ALLOWED = frozenset({"PAGE", "NUMPAGES", "SECTIONPAGES", "SECTION"})
_REVISIONS = frozenset(
    f"{W}{t}"
    for t in (
        "ins", "del", "moveFrom", "moveTo", "delText", "delInstrText", "rPrChange", "pPrChange",
        "sectPrChange", "tblPrChange", "tblGridChange", "trPrChange", "tcPrChange", "numberingChange",
        "cellIns", "cellDel", "cellMerge",
    )
)  # fmt: skip
_COMMENTS = frozenset(f"{W}{t}" for t in ("commentRangeStart", "commentRangeEnd", "commentReference", "comment"))
STYLES_PART = "word/styles.xml"
DOCUMENT_RELS = "word/_rels/document.xml.rels"
#: The styles the renderer reads by name when they are there (the starter's contract) and Word's base style.
RENDERER_STYLES = (
    "Normal",
    "SMD Body",
    "SMD Item Label",
    "SMD Item Text",
    "SMD Heading 1",
    "SMD Heading 2",
    "SMD Heading 3",
    "SMD Caption",
    "SMD Signature",
)


def clean(blob: bytes) -> bytes:
    """The firm's save without what a save adds that is not the look (step 1)."""
    from ..firmbase.adopt import clean_package
    from ..library_setup.make import drop_custom_xml, drop_document_links, drop_foreign_fields

    return drop_foreign_fields(drop_document_links(drop_custom_xml(clean_package(blob))))


def _fold(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _xml_parts(blob: bytes) -> Iterator[tuple[str, Any]]:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for name in z.namelist():
            if (
                name.endswith(".xml")
                and not name.startswith(("docProps/", "customXml/"))
                and name != "[Content_Types].xml"
            ):
                yield name, etree.fromstring(z.read(name))


def _paragraph_texts(root: Any) -> Iterator[str]:
    """Each paragraph's own text (a paragraph inside a text box is its own)."""
    for p in root.iter(f"{W}p"):
        yield "".join(t.text or "" for t in p.iter(f"{W}t") if _owner(t) is p)


def _owner(el: Any) -> Any:
    node = el.getparent()
    while node is not None and node.tag != f"{W}p":
        node = node.getparent()
    return node


def _pieces(name: str, text: str) -> list[str]:
    """The pieces of one paragraph the fixed strings must cover: the whole when it
    holds no marked place, else each piece between them; empty, punctuation and a
    header or footer's page numbering need no covering."""
    page_story = bool(_STORY_WITH_PAGES.match(name))
    whole = _fold(text)
    out = []
    for piece in [whole] if not _MARK.search(text) else [_fold(s) for s in _MARK.split(text)]:
        if not piece or _PUNCT_ONLY.match(piece) or (page_story and _PAGE_LINE.fullmatch(piece)):
            continue
        out.append(piece)
    return out


def fixed_of(accepted: bytes) -> frozenset[str]:
    """The slot's fixed strings: every word the accepted copy carries outside its
    marked places, as the root writer passed it."""
    found: set[str] = set()
    for name, root in _xml_parts(accepted):
        for text in _paragraph_texts(root):
            whole = _fold(text)
            found.update([whole] if whole and not _MARK.search(text) else [])
            found.update(_pieces(name, text))
    return frozenset(found)


def words_changed(blob: bytes, fixed: Iterable[str]) -> int:
    """How many paragraphs hold words that are not the slot's fixed strings."""
    allowed = frozenset(fixed)
    n = 0
    for name, root in _xml_parts(blob):
        for text in _paragraph_texts(root):
            if _fold(text) in allowed:
                continue
            if any(piece not in allowed for piece in _pieces(name, text)):
                n += 1
    return n


def _field_codes(root: Any) -> list[str]:
    codes = [el.get(f"{W}instr", "") for el in root.iter(f"{W}fldSimple")]
    current: list[str] = []
    for el in root.iter():
        if el.tag == f"{W}instrText":
            current.append(el.text or "")
        elif el.tag == f"{W}fldChar" and el.get(f"{W}fldCharType") in ("separate", "end") and current:
            codes.append("".join(current))
            current = []
    return codes + (["".join(current)] if current else [])


def _structure(blob: bytes) -> list[str]:
    out: set[str] = set()
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        if any(n.startswith("word/comments") for n in z.namelist()):
            out.add("comments")
    for _name, root in _xml_parts(blob):
        for el in root.iter():
            if el.tag in _REVISIONS:
                out.add("tracked_changes")
            elif el.tag in _COMMENTS:
                out.add("comments")
        if any((c.strip().split() or [""])[0].upper() not in _FIELDS_ALLOWED for c in _field_codes(root)):
            out.add("field_added")
    return sorted(out)


def _body_pictures(blob: bytes) -> int:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        if DOCUMENT_RELS not in z.namelist():
            return 0
        root = etree.fromstring(z.read(DOCUMENT_RELS))
    return sum(1 for r in root if r.get("Type", "").endswith("/image"))


def _body(blob: bytes) -> Any:
    return etree.fromstring(zipfile.ZipFile(io.BytesIO(blob)).read(DOCUMENT)).find(f"{W}body")


def _marked(kind: str, accepted: bytes, blob: bytes) -> list[str]:
    from ..firmbase import FirmBase, FirmBaseUnfit, Source
    from ..firmbase.markers import marked, unwritable

    body = _body(blob)
    if sorted(marked(body)) != sorted(marked(_body(accepted))):
        return ["marked_places_changed"]
    if unwritable(body):
        return ["marked_places_unwritable"]
    try:
        FirmBase.load(kind, Source("", "", "the firm's saved library file", ""), blob)
    except FirmBaseUnfit:
        return ["not_a_firm_base"]
    return []


def _style_names(blob: bytes) -> set[str]:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        if STYLES_PART not in z.namelist():
            return set()
        root = etree.fromstring(z.read(STYLES_PART))
    return {n.get(f"{W}val", "") for n in root.iter(f"{W}name")}


def _styles(accepted: bytes, blob: bytes) -> list[str]:
    body = _body(blob)
    sects = list(body.iter(f"{W}sectPr"))
    if len(sects) != 1:
        return ["not_one_section"]
    if sects[0].find(f"{W}pgSz") is None or sects[0].find(f"{W}pgMar") is None:
        return ["page_setup_missing"]
    needed = _style_names(accepted) & set(RENDERER_STYLES)
    return ["named_style_removed"] if needed - _style_names(blob) else []


def problems(kind: str, contract: str, accepted: bytes, cleaned: bytes) -> list[str]:
    """Every code the cleaned save is refused for against the accepted copy; empty when it passes."""
    if contract == PROTOTYPES or contract not in (MARKED, STYLES):
        return ["contract_not_checkable"]
    try:
        out = []
        if words_changed(cleaned, fixed_of(accepted)):
            out.append("words_changed")
        before = set(_structure(accepted))
        out += [c for c in _structure(cleaned) if c not in before]
        if _body_pictures(cleaned) > _body_pictures(accepted):
            out.append("body_picture_added")
        out += _marked(kind, accepted, cleaned) if contract == MARKED else _styles(accepted, cleaned)
    except (KeyError, ValueError, zipfile.BadZipFile, etree.XMLSyntaxError):
        return ["not_a_word_file"]
    return out


__all__ = [
    "MARKED",
    "PROTOTYPES",
    "RENDERER_STYLES",
    "STYLES",
    "clean",
    "fixed_of",
    "problems",
    "words_changed",
]
