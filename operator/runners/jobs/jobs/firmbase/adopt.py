"""Taking a firm's file as a base: whose file it is, and what must not travel.

Which file is a class's base (decided 2026-11-05): the attorney's most recent
ACCEPTED save of that class, his own Word save or the version he accepted in
writing, blanked. A firm-authored past file is the base only when no accepted save
exists, because his acceptance is newer than his old files: on 2026-11-04 every
firm-authored brief failed the rules he had just written.

``operator_authored`` reads the file itself, never a document system's history, which
names staff as the creator of files the Operator filed: its properties, and its
styles, since an attorney's Word save of our file puts his name in the properties and
keeps our styles (measured 2026-11-04: both saves carry them, no firm-authored brief
of 25 does). ``may_adopt``: such a file is a base only under a record of his acceptance
of exactly these bytes; an Operator render nobody accepted never is, or our layout
guesses become the firm's standard.

``clean_package``: what names another matter or a person does not travel into
the next client's document: the custom properties (a document system's matter
and file ids), the people, title and subject in the core properties, the titles
in the application properties, and the thumbnail, a picture of the source's first
page.
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from dataclasses import dataclass

from lxml import etree

from .base import Source

CORE = "docProps/core.xml"
CUSTOM = "docProps/custom.xml"
ROOT_RELS = "_rels/.rels"
_OPERATOR = re.compile(r"(?i)\bSMD\b|\boperator\b|python-docx")
_OUR_STYLES = re.compile(r"(?i)^(SMD |Brief ?(Body|Heading))")
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_APP = "docProps/app.xml"
_PEOPLE = {"creator", "lastModifiedBy", "title", "subject", "description", "keywords", "category"}
_THUMBNAIL = re.compile(r"^docProps/thumbnail\.\w+$")
DOCUMENT_RELS = "word/_rels/document.xml.rels"
_STORY = re.compile(r"^word/(header|footer|footnotes|endnotes|comments)\d*\.xml$")
#: What a page number's line says: its field results and "Page 1 of 2".
_KEEP_IN_STORY = re.compile(r"(?i)(\d+|page|of|\s)*")


def _serial(root: etree._Element) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _core(data: bytes) -> dict[str, str]:
    return {etree.QName(e).localname: e.text or "" for e in etree.fromstring(data)}


def operator_authored(blob: bytes) -> bool:
    """The file's properties name the Operator or its renderer, or it holds our styles."""
    z = zipfile.ZipFile(io.BytesIO(blob))
    core = _core(z.read(CORE))
    if any(_OPERATOR.search(core.get(k, "")) for k in ("creator", "lastModifiedBy")):
        return True
    styles = etree.fromstring(z.read("word/styles.xml"))
    return any(_OUR_STYLES.match(n.get(f"{_W}val") or "") for n in styles.iter(f"{_W}name"))


def may_adopt(blob: bytes, source: Source) -> bool:
    """A firm's own file, or an accepted save recorded for exactly these bytes."""
    if not operator_authored(blob):
        return True
    recorded = all((source.matter_id, source.file_id, source.version, source.accepted))
    return recorded and hashlib.sha256(blob).hexdigest() == source.sha256


@dataclass(frozen=True)
class Candidate:
    """A file that could be a class's base; ``saved_at`` is when it was last saved,
    ISO 8601 UTC, read from the document system."""

    source: Source
    blob: bytes
    saved_at: str


def choose(candidates: list[Candidate]) -> Candidate | None:
    """The rule: the most recent accepted save; a firm-authored past file only when no
    accepted save exists; an Operator render nobody accepted, never. None: no base, so
    the class keeps its config layout."""
    usable = [c for c in candidates if may_adopt(c.blob, c.source)]
    accepted = [c for c in usable if c.source.version and c.source.accepted]
    return max(accepted or usable, key=lambda c: c.saved_at, default=None)


def _blank_core(data: bytes) -> bytes:
    root = etree.fromstring(data)
    for el in root:
        if etree.QName(el).localname in _PEOPLE:
            el.text = ""
    return _serial(root)


def _empty_custom(data: bytes) -> bytes:
    root = etree.fromstring(data)
    for el in list(root):
        root.remove(el)
    return _serial(root)


def _blank_titles(data: bytes) -> bytes:
    """The source's own title, as Word lists it in the application properties."""
    root = etree.fromstring(data)
    for el in root.iter():
        if isinstance(el.tag, str) and etree.QName(el).localname == "TitlesOfParts":
            for leaf in el.iter():
                if isinstance(leaf.tag, str) and etree.QName(leaf).localname == "lpstr":
                    leaf.text = ""
    return _serial(root)


def _without_thumbnail(data: bytes) -> bytes:
    root = etree.fromstring(data)
    for rel in list(root):
        if "thumbnail" in rel.get("Type", ""):
            root.remove(rel)
    return _serial(root)


def _blank_story(data: bytes) -> bytes:
    """A header, footer, note or comment with no words of the matter: page numbering
    stays, whoever commented goes."""
    root = etree.fromstring(data)
    for t in root.iter(f"{_W}t"):
        if not _KEEP_IN_STORY.fullmatch(t.text or ""):
            t.text = ""
    for el in root.iter(f"{_W}comment"):
        for attr in ("author", "initials"):
            if el.get(f"{_W}{attr}"):
                el.set(f"{_W}{attr}", "")
    return _serial(root)


def _prune_rels(rels: bytes, document: bytes) -> tuple[bytes, set[str]]:
    """The document's picture and link relationships it no longer uses go; returns the
    targets dropped."""
    root = etree.fromstring(rels)
    gone = set()
    for rel in list(root):
        kind = rel.get("Type", "").rsplit("/", 1)[-1]
        if kind in ("image", "hyperlink", "oleObject") and b'"' + rel.get("Id", "").encode() + b'"' not in document:
            gone.add(rel.get("Target", ""))
            root.remove(rel)
    return _serial(root), gone


def _dropped_media(src: zipfile.ZipFile, gone: set[str]) -> set[str]:
    """The media parts named only by a dropped relationship."""
    used = b"".join(src.read(n) for n in src.namelist() if n.endswith(".rels") and n != DOCUMENT_RELS)
    return {f"word/{t}" for t in gone if t.startswith("media/") and t.encode() not in used}


def clean_package(blob: bytes, document: bytes | None = None, stories: bool = False) -> bytes:
    """``blob`` with its identifying properties cleared, ``document`` as its
    document part when given; every other part by its bytes. ``stories``: the words in
    the headers, footers, notes and comments go too, with any picture or link the new
    document no longer uses: a file whose every word was of one matter."""
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(blob)) as src, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        rels, dropped = None, set()
        if stories and document is not None and DOCUMENT_RELS in src.namelist():
            rels, gone = _prune_rels(src.read(DOCUMENT_RELS), document)
            dropped = _dropped_media(src, gone)
        for info in src.infolist():
            if _THUMBNAIL.match(info.filename) or info.filename in dropped:
                continue
            raw = src.read(info.filename)
            if info.filename == "word/document.xml" and document is not None:
                raw = document
            elif info.filename == DOCUMENT_RELS and rels is not None:
                raw = rels
            elif stories and _STORY.match(info.filename):
                raw = _blank_story(raw)
            elif info.filename == CORE:
                raw = _blank_core(raw)
            elif info.filename == CUSTOM:
                raw = _empty_custom(raw)
            elif info.filename == _APP:
                raw = _blank_titles(raw)
            elif info.filename == ROOT_RELS:
                raw = _without_thumbnail(raw)
            out.writestr(info, raw)
    return buf.getvalue()
