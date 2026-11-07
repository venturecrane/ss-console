"""The scrub check for the Word-saved marker fixtures (tests/fixtures/word_saved_markers/).

Each fixture is one paragraph whose run structure was saved by Word itself,
with every character of its text replaced by a marker and every identifier
replaced by a fixed made-up value. This check is what keeps it that way: it
fails a fixture holding anything that is not on a closed list.

Everything a fixture may contain is listed here, by name:

* its file name: a number word, then words from ``LABEL_WORDS``;
* its text: the marker ``[[fixture.<number word>]]`` and nothing else, cut
  across two or more text pieces; a deleted piece holds only ``#``; a field
  instruction holds only ``MADE_UP_INSTRUCTION``;
* its element and attribute names: ``ELEMENTS`` and the keys of the two
  attribute tables, in the namespaces of ``NAMESPACES``;
* its attribute values: for an attribute that can hold an identifier, exactly
  the made-up values in ``MADE_UP``; for any other, a short whole number or a
  value Word itself defines, in ``WORD_VALUES``.

A last sweep reads the raw file and fails any alphabetic word that is none of
the above, so a word in a place the lists do not model still fails.

Run it on a directory or on files::

    python tests/firmbase_word_saved_scrub.py tests/fixtures/word_saved_markers
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from lxml import etree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
XML_NS = "http://www.w3.org/XML/1998/namespace"
NAMESPACES = {W_NS: "w", W14_NS: "w14", R_NS: "r", XML_NS: "xml"}

NUMBER_WORDS = (
    "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen twenty twentyone"
).split()

# The words a fixture's file name may hold after its number word.
LABEL_WORDS = frozenset(
    "proof mark spelling grammar revision id run format bold underline superscript on part bookmark start end "
    "rendered page break tab in line two texts one no hyphen smart tag tracked insertion deletion hyperlink "
    "field content control identical runs".split()
)

# --- the fixed made-up values (the allow-list) ------------------------------
MADE_UP_RSIDS = frozenset(f"00A0000{n}" for n in range(1, 9))
MADE_UP_PARAGRAPH_ID = "0A000001"
MADE_UP_TEXT_ID = "0A000002"
MADE_UP_FONT = "Fixture Sans"
MADE_UP_AUTHOR = "Fixture Author"
MADE_UP_DATE = "2000-01-01T00:00:00Z"
MADE_UP_BOOKMARKS = frozenset({"bm1", "bm2"})
MADE_UP_RELATIONSHIP = "rId1"
MADE_UP_STYLE = "Body"
MADE_UP_INSTRUCTION = " REF bm1 "
DELETED_FILLER = "#"
SMART_TAG_URI = "urn:schemas-microsoft-com:office:smarttags"

# An attribute that can hold an identifier, a name, a date or a path: only
# these exact values.
MADE_UP: dict[str, frozenset[str]] = {
    "w:rsidR": MADE_UP_RSIDS,
    "w:rsidRPr": MADE_UP_RSIDS,
    "w:rsidRDefault": MADE_UP_RSIDS,
    "w:rsidP": MADE_UP_RSIDS,
    "w:rsidDel": MADE_UP_RSIDS,
    "w14:paraId": frozenset({MADE_UP_PARAGRAPH_ID}),
    "w14:textId": frozenset({MADE_UP_TEXT_ID}),
    "w:ascii": frozenset({MADE_UP_FONT}),
    "w:hAnsi": frozenset({MADE_UP_FONT}),
    "w:cs": frozenset({MADE_UP_FONT}),
    "w:eastAsia": frozenset({MADE_UP_FONT}),
    "w:author": frozenset({MADE_UP_AUTHOR}),
    "w:date": frozenset({MADE_UP_DATE}),
    "w:name": MADE_UP_BOOKMARKS,
    "w:anchor": MADE_UP_BOOKMARKS,
    "r:id": frozenset({MADE_UP_RELATIONSHIP}),
    "w:uri": frozenset({SMART_TAG_URI}),
    "w:element": frozenset({"State", "place"}),
    "xml:space": frozenset({"preserve"}),
}

# Any other attribute: a whole number of at most six digits, or one of the
# values Word itself defines (plus the one made-up style id).
PLAIN_ATTRIBUTES = frozenset(
    "w:val w:type w:fldCharType w:fldLock w:history w:id w:pos w:line w:lineRule w:after w:before w:left w:right "
    "w:firstLine w:hanging".split()
)
WORD_VALUES = frozenset(
    "spellStart spellEnd gramStart gramEnd begin separate end single both center left right clear auto exact "
    "atLeast superscript Normal Heading1 Hyperlink ListParagraph".split()
) | {MADE_UP_STYLE}
WHOLE_NUMBER = re.compile(r"^-?[0-9]{1,6}$")

ELEMENTS = frozenset(
    "w:p w:pPr w:rPr w:r w:t w:tab w:tabs w:br w:noBreakHyphen w:lastRenderedPageBreak w:proofErr "
    "w:bookmarkStart w:bookmarkEnd w:hyperlink w:fldChar w:instrText w:smartTag w:ins w:del w:delText "
    "w:sdt w:sdtPr w:sdtEndPr w:sdtContent "
    "w:pStyle w:rStyle w:rFonts w:b w:bCs w:u w:vertAlign w:color w:sz w:szCs w:noProof w:jc w:ind w:spacing "
    "w:numPr w:ilvl w:numId w:outlineLvl w:widowControl w:contextualSpacing w:suppressAutoHyphens".split()
)

_T = f"{{{W_NS}}}t"
_INSTR = f"{{{W_NS}}}instrText"
_DEL_TEXT = f"{{{W_NS}}}delText"
_P = f"{{{W_NS}}}p"
_DECLARATION = re.compile(rb"^<\?xml version='1\.0' encoding='UTF-8' standalone='yes'\?>\n")
_WORD = re.compile(r"[A-Za-z0-9]*[A-Za-z][A-Za-z0-9]*")
_TEXT_PIECE = re.compile(r"(?<=<w:t>)[^<]*(?=</w:t>)|(?<=<w:t xml:space=\"preserve\">)[^<]*(?=</w:t>)")


def _name(qualified: str) -> str | None:
    q = etree.QName(qualified)
    prefix = NAMESPACES.get(q.namespace or "")
    return f"{prefix}:{q.localname}" if prefix else None


def _allowed_words() -> frozenset[str]:
    """Every alphabetic word a clean fixture can hold: the XML names, the
    marker words, and the words of the made-up and Word-defined values."""
    words: set[str] = {"fixture", *NUMBER_WORDS, *NAMESPACES.values(), "xmlns"}
    values = {v for allowed in MADE_UP.values() for v in allowed} | WORD_VALUES | {MADE_UP_INSTRUCTION}
    for text in [*ELEMENTS, *MADE_UP, *PLAIN_ATTRIBUTES, *values]:
        words.update(_WORD.findall(text))
    return frozenset(words)


def marker_for(file_name: str) -> str | None:
    """The marker a fixture file must hold, from its name; None for a name
    that is not a fixture name."""
    stem, dot, suffix = file_name.rpartition(".")
    number, *label = stem.split("-")
    if suffix != "xml" or not dot or number not in NUMBER_WORDS or not label or not set(label) <= LABEL_WORDS:
        return None
    return f"[[fixture.{number}]]"


def _raw_problems(blob: bytes) -> list[str]:
    out: list[str] = []
    try:
        raw = blob.decode("ascii")
    except UnicodeDecodeError:
        return ["a character outside plain ASCII"]
    if not _DECLARATION.match(blob):
        out.append("does not start with the one fixed XML declaration")
    body = raw.split("\n", 1)[1] if "\n" in raw else ""
    for token, what in (
        ("<!", "a comment, DOCTYPE or CDATA section"),
        ("<?", "a processing instruction"),
        ("&", "an entity"),
    ):
        if token in body:
            out.append(f"holds {what}")
    for uri in (*NAMESPACES, SMART_TAG_URI, MADE_UP_DATE):
        body = body.replace(uri, " ")
    # The marker's own pieces are not words; _text_problems holds them to the marker exactly.
    body = _TEXT_PIECE.sub(" ", body)
    allowed = _allowed_words()
    strange = sorted({w for w in _WORD.findall(body) if w not in allowed})
    if strange:
        out.append(f"words that are on no list: {', '.join(strange)}")
    return out


def _attribute_problems(el: etree._Element) -> list[str]:
    out: list[str] = []
    for key, value in el.attrib.items():
        name = _name(key)
        if name in MADE_UP:
            if value not in MADE_UP[name]:
                out.append(f"{name} holds a value that is not one of its made-up values")
        elif name in PLAIN_ATTRIBUTES:
            if not (WHOLE_NUMBER.match(value) or value in WORD_VALUES):
                out.append(f"{name} holds a value that is neither a short number nor a Word value")
        else:
            out.append(f"attribute {name or key} is on no list")
    return out


def _text_problems(root: etree._Element, marker: str) -> list[str]:
    out: list[str] = []
    pieces = [t.text or "" for t in root.iter(_T)]
    if "".join(pieces) != marker:
        out.append(f"its text is not exactly the marker {marker}")
    if len(pieces) < 2 or not all(pieces):
        out.append("the marker is not cut across two or more text pieces")
    instruction = "".join(t.text or "" for t in root.iter(_INSTR))
    if instruction.replace(MADE_UP_INSTRUCTION, ""):
        out.append("a field instruction is not the made-up one")
    for el in root.iter():
        if el.tag in (_T, _INSTR) or not isinstance(el.tag, str):
            continue
        text = (el.text or "") + (el.tail or "")
        if el.tag == _DEL_TEXT:
            text = (el.text or "").replace(DELETED_FILLER, "") + (el.tail or "")
            if not el.text:
                out.append("a deleted piece is empty")
        if text.strip():
            out.append(f"text outside the marker, in or after {_name(el.tag)}")
    for t in root.iter(_T, _INSTR):
        if (t.tail or "").strip():
            out.append("text after a text element")
    return out


def problems(file_name: str, blob: bytes) -> list[str]:
    """What is wrong with one fixture; empty when it is clean."""
    marker = marker_for(file_name)
    if marker is None:
        return ["its file name is not a number word followed by listed label words, ending .xml"]
    out = _raw_problems(blob)
    try:
        root = etree.fromstring(blob, etree.XMLParser(resolve_entities=False, no_network=True, remove_comments=False))
    except etree.XMLSyntaxError:
        return [*out, "is not well-formed XML"]
    if root.tag != _P:
        out.append("its root is not one w:p")
    for el in root.iter():
        if not isinstance(el.tag, str):
            out.append("holds a comment or processing instruction")
            continue
        for uri in el.nsmap.values():
            if uri not in NAMESPACES:
                out.append("declares a namespace that is on no list")
        name = _name(el.tag)
        if name not in ELEMENTS:
            out.append(f"element {name or 'in an unlisted namespace'} is on no list")
        if el is not root and el.tag == _P:
            out.append("holds a second paragraph")
        out += _attribute_problems(el)
    return out + _text_problems(root, marker)


def fixture_files(directory: Path) -> list[Path]:
    return sorted(p for p in directory.iterdir() if p.suffix == ".xml")


def main(argv: list[str]) -> int:
    paths: list[Path] = []
    for arg in argv:
        p = Path(arg)
        paths += fixture_files(p) if p.is_dir() else [p]
    if not paths:
        print("no fixtures given")
        return 2
    bad = 0
    for p in paths:
        found = problems(p.name, p.read_bytes())
        if found:
            bad += 1
            for line in found:
                print(f"DIRTY {p.name}: {line}")
    print(f"{len(paths) - bad} of {len(paths)} fixtures clean")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
