"""Making a firm's STYLES base from one of its filed Word documents, and proving
it carries none of that document's case. Pure: bytes in, bytes out.

A styles base gives a drafted document the firm's page setup, headers,
footers, pleading paper, default font and named styles; the renderer writes the
body fresh (``render.render(base=...)``), so unlike a marked base nothing of the
body is kept. THE STEPS, IN ORDER, EACH FAIL CLOSED:

1. Opened the way the renderer opens a base (``docx_base.open_as_base``): the
   body cleared to its final section properties. A file of more than one section
   is refused (``multi_section``), as the renderer refuses it.
2. The pictures the cleared body no longer shows go, then custom XML, the links
   to a template or document system (document variables included) and every
   header or footer field that is not page numbering (``make.py``'s own steps).
3. The properties that name a person or a matter are cleared (title, subject,
   author, last saved by, keywords, description, category, company, manager,
   the custom properties, the thumbnail), and the notes and comments, which the
   cleared body no longer points at, are emptied.
4. What the headers and footers still say is judged against ``make.py``'s allow
   list (``judge_stories``): page numbering, the firm's own identity, and the
   label and running-title words. Anything else refuses the file
   (``case_words_remain``) and nothing is written.

Only counts and reason codes come back, never a word of the document.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from typing import Iterable

from lxml import etree

from . import make as make_mod
from .make import Unusable

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_NOTES = re.compile(r"^word/(footnotes|endnotes|comments)\d*\.xml$")
_HEAD_FOOT = re.compile(r"^word/(header|footer)\d*\.xml$")
APP = "docProps/app.xml"
#: The application properties that can name a person or a firm's client.
_APP_PEOPLE = frozenset({"Company", "Manager", "HyperlinkBase"})


@dataclass(frozen=True)
class StylesMade:
    data: bytes
    residue: dict[str, int] = field(default_factory=dict)
    dropped: dict[str, int] = field(default_factory=dict)
    #: Footer lines naming the source document's kind (its running title); each
    #: draft made in the base prints its own title there (``pleading.retitle_footer``).
    running_title_lines: int = 0


def _cleared_body(kind: str, blob: bytes) -> bytes:
    from smokeball_connector.docx_base import open_as_base
    from smokeball_connector.docx_format_types import FormatRefused, FormatReport

    try:
        doc = open_as_base(blob, FormatReport(kind))
    except FormatRefused as exc:
        code = "multi_section" if "sections" in str(exc) else "not_a_word_file"
        raise Unusable(code) from exc
    except Exception as exc:
        raise Unusable("not_a_word_file", type(exc).__name__) from exc
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _serial(root: etree._Element) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _blank_notes_and_people(blob: bytes) -> bytes:
    """Notes and comments emptied of every word (the body that pointed at them is
    gone), deleted text in a header or footer dropped, and the application
    properties that name a person or a company blanked. The core and custom
    properties and the thumbnail go by ``firmbase.adopt.clean_package``."""
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(blob)) as src, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for info in src.infolist():
            raw = src.read(info.filename)
            if _NOTES.match(info.filename) or _HEAD_FOOT.match(info.filename):
                root = etree.fromstring(raw)
                tags = (f"{_W}t", f"{_W}delText") if _NOTES.match(info.filename) else (f"{_W}delText",)
                for el in root.iter(*tags):
                    el.text = ""
                for el in root.iter(f"{_W}comment"):
                    for attr in ("author", "initials"):
                        if el.get(f"{_W}{attr}"):
                            el.set(f"{_W}{attr}", "")
                raw = _serial(root)
            elif info.filename == APP:
                root = etree.fromstring(raw)
                for el in root:
                    if etree.QName(el).localname in _APP_PEOPLE:
                        el.text = ""
                raw = _serial(root)
            out.writestr(info, raw)
    return buf.getvalue()


def _body_words(blob: bytes) -> int:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        root = etree.fromstring(z.read("word/document.xml"))
    return sum(1 for t in root.iter(f"{_W}t") if (t.text or "").strip())


def make_styles(kind: str, blob: bytes, identity: Iterable[str] = ()) -> StylesMade:
    """The styles base for ``kind`` made from ``blob`` (the module docstring).
    Raises ``Unusable`` with a code; never returns a file that kept a client word."""
    from ..firmbase.adopt import clean_package

    cleared = _cleared_body(kind, blob)
    removed = make_mod.dropped(cleared)
    made, removed["body_pictures"] = make_mod.drop_body_pictures(cleared)
    made = make_mod.drop_foreign_fields(make_mod.drop_document_links(make_mod.drop_custom_xml(made)))
    made = _blank_notes_and_people(clean_package(made))
    _pictures_gone, lost = make_mod.picture_parts(blob, made)
    if lost:
        raise Unusable("header_picture_lost", f"{lost} picture(s) of a header or footer")
    if _body_words(made):
        raise Unusable("body_not_cleared")
    _fixed, residue = make_mod.judge_stories(made, identity)
    from ..drafting.pleading import running_title_lines

    return StylesMade(made, residue, removed, running_title_lines(made))


__all__ = ["StylesMade", "make_styles"]
