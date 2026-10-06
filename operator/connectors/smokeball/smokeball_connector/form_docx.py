"""The firm's own Word form, filled in place: pure functions over .docx bytes.

WHY THIS EXISTS. The drafting renderer (``docx_format``) keeps a template's
page setup and styles but REPLACES its body with composed content. A firm's
form letter is the opposite artifact: the body IS the firm's text, word for
word, and only the merged values change. Christa (A&P, 2026-10-05) asked for
rep letters "in that exact formatting", so the form is opened, its
``{{field}}`` placeholders are replaced, and every other byte of the package is
copied through untouched: headers, footers, styles, numbering, section setup,
relationships, the body's own paragraphs and runs.

THE PLACEHOLDER CONTRACT. A form carries ``{{name}}`` placeholders, each built
into a single run by ``build_form`` (so its formatting is that run's). Filling
is still defensive about runs: Word splits text across runs whenever a person
edits it (a spell-check mark, a revision id), and a firm editing its own form
in Word is the expected case, so a placeholder split across runs is found on
the paragraph's text and rewritten into its first run.

Two kinds of field:

* A PARAGRAPH field (``paragraph_fields``) repeats its whole paragraph once per
  line of its value: "VIA FAX: ..." and "Email: ..." each on their own line, in
  the paragraph's own indentation.
* Any other field may carry line breaks, written as ``w:br`` inside its run,
  the way the firm's own carrier block is laid out (name, then address lines).

Nothing here reads a matter or decides a value; ``form_letters`` does that.
Pure: no client, no clock, no I/O beyond the bytes passed in.
"""

from __future__ import annotations

import copy
import io
import re
import zipfile
from dataclasses import dataclass, field
from typing import Callable

from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
W_P = f"{{{W}}}p"
W_R = f"{{{W}}}r"
W_T = f"{{{W}}}t"
W_BR = f"{{{W}}}br"
W_CR = f"{{{W}}}cr"
W_TAB = f"{{{W}}}tab"
W_RPR = f"{{{W}}}rPr"
W_FLDCHAR = f"{{{W}}}fldChar"
W_INSTR = f"{{{W}}}instrText"
W_FLDSIMPLE = f"{{{W}}}fldSimple"

DOCUMENT_PART = "word/document.xml"
PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

#: The custom-properties part, emptied in a built form. A letter generated from
#: a Smokeball form carries the SOURCE matter's binding (``MatterId``,
#: ``MatterFileId``, ``AccountId``) here; a form built from one client's letter
#: must not carry that client's matter into every letter filled from it.
CUSTOM_PROPS_PART = "docProps/custom.xml"
CORE_PROPS_PART = "docProps/core.xml"
#: Smokeball keeps a form's "ask" answers as document variables in the settings
#: part, and the answer to "which party?" is the SOURCE matter's id. The asks
#: themselves are field codes ``unwrap_fields`` removes, so the variables go too.
SETTINGS_PART = "word/settings.xml"
_DOC_VARS = re.compile(rb"<w:docVars>.*?</w:docVars>|<w:docVars/>", re.S)
_EMPTY_CUSTOM_PROPS = (
    b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
    b'<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties" '
    b'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"/>'
)


class FormError(ValueError):
    """The bytes are not a form this module can fill or build."""


# ---- Reading and writing the package --------------------------------------


def _read_parts(blob: bytes) -> tuple[list[zipfile.ZipInfo], dict[str, bytes]]:
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            infos = zf.infolist()
            parts = {i.filename: zf.read(i.filename) for i in infos}
    except zipfile.BadZipFile as exc:
        raise FormError("not a .docx (the file is not a zip package)") from exc
    if DOCUMENT_PART not in parts:
        raise FormError(f"not a .docx (no {DOCUMENT_PART})")
    return infos, parts


def _write_parts(infos: list[zipfile.ZipInfo], parts: dict[str, bytes]) -> bytes:
    """The package again, every entry in its original order and compression."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        for info in infos:
            zf.writestr(info, parts[info.filename])
    return out.getvalue()


def _parse(xml: bytes) -> tuple[bytes, etree._Element]:
    """The declaration bytes exactly as the part wrote them (Word ends it with
    CRLF), and the parsed root. Kept so an untouched part round-trips."""
    root = etree.fromstring(xml)
    head = xml[: xml.find(b"<", xml.find(b"?>"))] if xml.startswith(b"<?xml") else b""
    return head, root


def _serialize(head: bytes, root: etree._Element) -> bytes:
    return head + etree.tostring(root, encoding="UTF-8", xml_declaration=False)


# ---- Paragraph text, run by run --------------------------------------------


@dataclass
class _Seg:
    run: etree._Element
    elem: etree._Element
    text: str
    start: int


def _run_items(run: etree._Element) -> list[tuple[etree._Element, str]]:
    items = []
    for child in run:
        if child.tag == W_T:
            items.append((child, child.text or ""))
        elif child.tag in (W_BR, W_CR):
            items.append((child, "\n"))
        elif child.tag == W_TAB:
            items.append((child, "\t"))
    return items


def _segments(paragraph: etree._Element) -> tuple[list[_Seg], str]:
    segs: list[_Seg] = []
    text = ""
    for run in paragraph.iter(W_R):
        for elem, piece in _run_items(run):
            segs.append(_Seg(run, elem, piece, len(text)))
            text += piece
    return segs, text


def paragraph_text(paragraph: etree._Element) -> str:
    """The paragraph as a reader sees it: line breaks as ``\\n``, tabs as ``\\t``."""
    return _segments(paragraph)[1]


def _t(text: str) -> etree._Element:
    elem = etree.Element(W_T)
    elem.set(_XML_SPACE, "preserve")
    elem.text = text
    return elem


def _items_for(text: str) -> list[etree._Element]:
    """Run content for ``text``: ``\\n`` a line break, ``\\t`` a tab."""
    out: list[etree._Element] = []
    for i, line in enumerate(text.split("\n")):
        if i:
            out.append(etree.Element(W_BR))
        for j, piece in enumerate(line.split("\t")):
            if j:
                out.append(etree.Element(W_TAB))
            if piece:
                out.append(_t(piece))
    return out


def _has_content(run: etree._Element) -> bool:
    return any(child.tag != W_RPR for child in run)


def replace_in_paragraph(paragraph: etree._Element, old: str, new: str) -> bool:
    """Replace the first ``old`` in the paragraph's text with ``new``, wherever
    the runs split it. The new text goes into the FIRST run the match touched,
    so it takes that run's formatting; text before and after the match keeps
    its own run. Runs the match emptied are removed. False when absent."""
    segs, text = _segments(paragraph)
    at = text.find(old) if old else -1
    if at < 0:
        return False
    end = at + len(old)
    touched = [s for s in segs if s.start < end and s.start + len(s.text) > at]
    first, last = touched[0], touched[-1]
    lead = first.text[: at - first.start] if first.elem.tag == W_T else ""
    tail = last.text[end - last.start :] if last.elem.tag == W_T else ""
    position = first.run.index(first.elem)
    for offset, elem in enumerate(([_t(lead)] if lead else []) + _items_for(new)):
        first.run.insert(position + offset, elem)
    if tail:
        last.run.insert(last.run.index(last.elem) + 1, _t(tail))
    for seg in touched:
        seg.run.remove(seg.elem)
    for run in {id(s.run): s.run for s in touched}.values():
        if run is not first.run and not _has_content(run) and run.getparent() is not None:
            run.getparent().remove(run)
    return True


# ---- Fields Word computes, which a form must not carry ----------------------


def unwrap_fields(root: etree._Element) -> int:
    """Remove every field's code, keeping what it displayed. A Smokeball form
    letter merges through locked ``AUTOMATIONFIELD`` fields bound to the source
    matter, and its date is a live ``DATE`` field that Word re-renders to the
    day it is opened or printed; a placeholder inside either would be replaced
    by Word, not by us. Returns how many field parts were removed."""
    removed = 0
    for run in list(root.iter(W_R)):
        if any(child.tag in (W_FLDCHAR, W_INSTR) for child in run):
            run.getparent().remove(run)
            removed += 1
    for simple in list(root.iter(W_FLDSIMPLE)):
        parent = simple.getparent()
        position = parent.index(simple)
        for child in list(simple):
            parent.insert(position, child)
            position += 1
        parent.remove(simple)
        removed += 1
    return removed


# ---- Filling ---------------------------------------------------------------


@dataclass
class FillResult:
    data: bytes
    placeholders: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)


def placeholders_in(blob: bytes) -> list[str]:
    """The distinct placeholder names a form's body carries, in order."""
    _infos, parts = _read_parts(blob)
    _head, root = _parse(parts[DOCUMENT_PART])
    seen: list[str] = []
    for paragraph in root.iter(W_P):
        for name in PLACEHOLDER_RE.findall(paragraph_text(paragraph)):
            if name not in seen:
                seen.append(name)
    return seen


def _expand_paragraph_field(root: etree._Element, name: str, value: str) -> None:
    """One copy of each paragraph holding ``{{name}}`` per line of ``value``."""
    lines = value.split("\n") or [""]
    for paragraph in list(root.iter(W_P)):
        if not any(m == name for m in PLACEHOLDER_RE.findall(paragraph_text(paragraph))):
            continue
        parent = paragraph.getparent()
        position = parent.index(paragraph)
        for offset, line in enumerate(lines):
            clone = copy.deepcopy(paragraph)
            _fill_paragraph(clone, {name: line})
            parent.insert(position + offset, clone)
        parent.remove(paragraph)


def _fill_paragraph(paragraph: etree._Element, values: dict[str, str]) -> list[str]:
    """Fill every known placeholder in one paragraph; return unknown names."""
    unknown: list[str] = []
    for _ in range(64):  # a paragraph never holds more; bounds a pathological form
        match = next(
            (m for m in PLACEHOLDER_RE.finditer(paragraph_text(paragraph)) if m.group(1) in values),
            None,
        )
        if match is None:
            break
        replace_in_paragraph(paragraph, match.group(0), values[match.group(1)])
    for name in PLACEHOLDER_RE.findall(paragraph_text(paragraph)):
        if name not in values:
            unknown.append(name)
    return unknown


def fill_form(blob: bytes, values: dict[str, str], paragraph_fields: frozenset[str] = frozenset()) -> FillResult:
    """The form with every placeholder replaced. Only ``word/document.xml``
    changes; every other part is copied byte for byte. A placeholder the form
    carries and ``values`` does not name is left in place and reported in
    ``unknown`` (the caller decides what a reader sees there)."""
    infos, parts = _read_parts(blob)
    head, root = _parse(parts[DOCUMENT_PART])
    found = placeholders_in(blob)
    for name in paragraph_fields:
        if name in values:
            _expand_paragraph_field(root, name, values[name])
    unknown: list[str] = []
    for paragraph in list(root.iter(W_P)):
        for name in _fill_paragraph(paragraph, values):
            if name not in unknown:
                unknown.append(name)
    parts[DOCUMENT_PART] = _serialize(head, root)
    return FillResult(data=_write_parts(infos, parts), placeholders=found, unknown=unknown)


def document_paragraphs(blob: bytes) -> list[str]:
    """Every body paragraph's text, in order (tests and read-backs compare these)."""
    _infos, parts = _read_parts(blob)
    _head, root = _parse(parts[DOCUMENT_PART])
    return [paragraph_text(p) for p in root.iter(W_P)]


# ---- Building a form from a filled letter ----------------------------------


def _drop_paragraph(root: etree._Element, text: str) -> None:
    hits = [p for p in root.iter(W_P) if paragraph_text(p).strip() == text.strip()]
    if len(hits) != 1:
        raise FormError(f"expected exactly one paragraph reading {text!r}, found {len(hits)}")
    hits[0].getparent().remove(hits[0])


def _replace_once(root: etree._Element, old: str, new: str) -> None:
    hits = [p for p in root.iter(W_P) if old in paragraph_text(p)]
    if len(hits) != 1 or paragraph_text(hits[0]).count(old) != 1:
        raise FormError(f"expected the text {old!r} exactly once in the letter, found {len(hits)} paragraphs")
    replace_in_paragraph(hits[0], old, new)


def _blank_core_people(xml: bytes) -> bytes:
    """``creator`` and ``lastModifiedBy`` name whoever made and last saved the
    source letter, not the firm's form."""
    xml = re.sub(rb"<dc:creator>[^<]*</dc:creator>", b"<dc:creator></dc:creator>", xml)
    return re.sub(rb"<cp:lastModifiedBy>[^<]*</cp:lastModifiedBy>", b"<cp:lastModifiedBy></cp:lastModifiedBy>", xml)


def rewrite_document(blob: bytes, edit: Callable[[etree._Element], None]) -> bytes:
    """The package with ``edit`` applied to the parsed body; every other part
    is copied byte for byte. A form builder that must see the field codes
    before ``build_form`` unwraps them works through this."""
    infos, parts = _read_parts(blob)
    head, root = _parse(parts[DOCUMENT_PART])
    edit(root)
    parts[DOCUMENT_PART] = _serialize(head, root)
    return _write_parts(infos, parts)


def build_form(source: bytes, replacements: list[tuple[str, str]], drop_paragraphs: list[str]) -> bytes:
    """A form from one of the firm's filled letters: field codes unwrapped,
    each merged value replaced by its placeholder (every ``old`` must occur
    exactly once), the listed paragraphs removed, and the source matter's
    binding emptied from the document properties and the settings part's
    document variables. Every other part is copied byte for byte."""
    infos, parts = _read_parts(source)
    head, root = _parse(parts[DOCUMENT_PART])
    unwrap_fields(root)
    for text in drop_paragraphs:
        _drop_paragraph(root, text)
    for old, new in replacements:
        _replace_once(root, old, new)
    parts[DOCUMENT_PART] = _serialize(head, root)
    if CUSTOM_PROPS_PART in parts:
        parts[CUSTOM_PROPS_PART] = _EMPTY_CUSTOM_PROPS
    if CORE_PROPS_PART in parts:
        parts[CORE_PROPS_PART] = _blank_core_people(parts[CORE_PROPS_PART])
    if SETTINGS_PART in parts:
        parts[SETTINGS_PART] = _DOC_VARS.sub(b"", parts[SETTINGS_PART])
    return _write_parts(infos, parts)


__all__ = [
    "CUSTOM_PROPS_PART",
    "DOCUMENT_PART",
    "PLACEHOLDER_RE",
    "FillResult",
    "FormError",
    "build_form",
    "document_paragraphs",
    "fill_form",
    "paragraph_text",
    "placeholders_in",
    "replace_in_paragraph",
    "rewrite_document",
    "unwrap_fields",
]
