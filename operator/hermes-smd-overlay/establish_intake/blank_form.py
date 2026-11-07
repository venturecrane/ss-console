"""Does an accepted library file hold any client text? A check on its bytes.

WHY (design 2026-11-06). The seat keeps its own copy of every file a firm
accepts as the file a kind of document is made in, and that copy outlives every
matter. An accepted file is a BLANKED FORM: for a ``marked`` file every word
outside its fixed parts was replaced by marked places (``[[name]]``, the
firm-base code; ``{{name}}`` fields too), and for a ``styles`` file the body is
written fresh, so only page setup, header, footer and styles are kept. "It holds
no client text" is a claim about bytes, so the writer runs this before it puts a
copy in the vault and refuses on any problem.

BUILT ON WHAT ADOPTION ALREADY CLEARS. ``jobs/firmbase/adopt.py``
``clean_package`` is the existing record of what names a person or a matter in a
Word package: the people, title and subject in the core properties, every
custom property (a document system's matter and file ids), the titles in the
application properties, the thumbnail (a picture of the first page), and the
words in the headers, footers, notes and comments, page numbering aside. This
check REFUSES a package where any of those still carries something, rather than
clearing it, because the bytes accepted are the bytes kept.

WHAT A PASS PROVES, exactly:

* the package is a Word document (``.docx`` or ``.dotx`` main part; no macros);
* every text run in every XML part, read a paragraph's whole text at a time, is
  a marked place, punctuation, page numbering in a header or footer, or one of
  the fixed strings the acceptance declares (whitespace folded, exact match);
* every field is a page-number field (PAGE, NUMPAGES, SECTIONPAGES, SECTION);
* no tracked change, deletion or formatting revision; no comment, comment mark
  or people list; no text box outside a header or footer (pleading paper's line
  numbers sit in one there, held to the page-numbering-or-fixed rule); no custom XML part, custom XML element or data
  binding; no embedded object or alternative-format chunk; no document variable,
  mail merge or attached template; no external relationship;
* the core properties' people, title, subject, description, keywords and
  category are empty; no custom property; the application properties' titles
  and manager are empty; no thumbnail; picture alt text and content-control
  names are empty, fixed strings, or marked-place names.

WHAT IT CANNOT PROVE:

* that a declared fixed string is not itself client text. The allow list comes
  with the acceptance; the check proves only that nothing outside it is there;
* anything inside a picture (a scanned letterhead, a photo): pictures are bytes
  this check does not read;
* that page-number-shaped text in a header or footer (digits, "page", "of") is a
  page number and not, say, a phone number;
* the application properties' company name, which is kept (it is the firm);
* that the marked places are the right ones for the kind, or that the layout is
  the firm's: that is the firm-base proof's job, not this one.

STANDARD LIBRARY ONLY: the intake runs as root in the overlay's environment.
The package is opened through ``safe_package.py``: a zip bounded on its
directory before any part is read, and an XML parser that refuses any DTD or
entity rather than expanding or fetching it; a refused part is a problem here.
Problems name the part and the place, never the text found there, because a
refusal's reasons travel back to the agent.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from establish_intake.safe_package import Node, Package, PackageRefused, open_package, parse_xml

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_CT = "{http://schemas.openxmlformats.org/package/2006/content-types}"

#: Main document content types this check takes. A macro-enabled document is not
#: a form; its code is text this check cannot read.
MAIN_TYPES = frozenset(
    {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml",
    }
)
DOCUMENT = "word/document.xml"
CORE = "docProps/core.xml"
CUSTOM = "docProps/custom.xml"
APP = "docProps/app.xml"

#: A marked place, read inside a paragraph's whole text: ``[[name]]`` (the
#: firm-base slot) or ``{{name}}`` (its field form).
_MARK = re.compile(r"\[\[[a-z0-9._]+\]\]|\{\{[a-z][a-z0-9._]*\}\}")
_MARK_NAME = re.compile(r"\A[a-z0-9._]+\Z")
#: What a page number's line says (``adopt.py`` ``_KEEP_IN_STORY``).
_PAGE_LINE = re.compile(r"(?i)(\d+|page|of|\s)*")
_PUNCT_ONLY = re.compile(r"\A[\s\W_]*\Z")
_STORY_WITH_PAGES = re.compile(r"\Aword/(header|footer)\d*\.xml\Z")
_FIELDS_ALLOWED = frozenset({"PAGE", "NUMPAGES", "SECTIONPAGES", "SECTION"})
#: The core properties ``adopt.py`` ``_PEOPLE`` blanks.
_CORE_PEOPLE = (
    "creator",
    "lastModifiedBy",
    "title",
    "subject",
    "description",
    "keywords",
    "category",
)

_REVISIONS = frozenset(
    f"{_W}{tag}"
    for tag in (
        "ins",
        "del",
        "moveFrom",
        "moveTo",
        "delText",
        "delInstrText",
        "rPrChange",
        "pPrChange",
        "sectPrChange",
        "tblPrChange",
        "tblGridChange",
        "trPrChange",
        "tcPrChange",
        "numberingChange",
        "cellIns",
        "cellDel",
        "cellMerge",
    )
)
_COMMENT_MARKS = frozenset(
    f"{_W}{t}" for t in ("commentRangeStart", "commentRangeEnd", "commentReference")
)
_EMBEDS = frozenset(f"{_W}{t}" for t in ("altChunk", "object", "embed", "control"))
_SETTINGS = frozenset(f"{_W}{t}" for t in ("docVars", "docVar", "mailMerge", "attachedTemplate"))
_TEXT_BOX_LOCAL = frozenset({"txbxContent", "textbox", "txbx"})


@dataclass(frozen=True)
class BlankCheck:
    """The check's answer. ``problems`` empty means it passed."""

    problems: tuple[str, ...]
    fixed_used: tuple[str, ...] = ()
    parts_read: int = 0
    marks: tuple[str, ...] = field(default=())

    @property
    def ok(self) -> bool:
        return not self.problems


def _fold(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


# ---- the package -------------------------------------------------------------------


def _open(blob: bytes) -> tuple[Package | None, list[str]]:
    try:
        z = open_package(blob)
    except PackageRefused as exc:
        return None, [str(exc)]
    if "[Content_Types].xml" not in z.names or DOCUMENT not in z.names:
        return None, ["the file is not a Word package (no content types or no document part)"]
    return z, []


def _parse(z: Package, name: str, problems: list[str]) -> Node | None:
    """The part's tree, or None with the reason appended to ``problems``."""
    try:
        return parse_xml(z.read(name))
    except PackageRefused as exc:
        problems.append(f"{name}: {exc}")
        return None


def _main_type_problems(z: Package) -> list[str]:
    problems: list[str] = []
    root = _parse(z, "[Content_Types].xml", problems)
    if root is None:
        return problems
    main = [
        o.get("ContentType", "")
        for o in root.iter(f"{_CT}Override")
        if o.get("PartName") == f"/{DOCUMENT}"
    ]
    if not main or main[0] not in MAIN_TYPES:
        return [
            "the main part is not a Word document or template (a macro-enabled file is refused)"
        ]
    return []


def _structure_problems(names: Iterable[str]) -> list[str]:
    out: list[str] = []
    for name in sorted(names):
        lower = name.lower()
        if lower.startswith("customxml/"):
            out.append(f"{name}: a custom XML part")
        elif lower.startswith("word/embeddings/") or lower.endswith("vbaproject.bin"):
            out.append(f"{name}: an embedded object or macro project")
        elif lower == "word/people.xml":
            out.append(f"{name}: the list of people who commented or revised")
        elif lower.startswith("docprops/thumbnail"):
            out.append(f"{name}: a thumbnail, a picture of the first page")
    return out


# ---- text ---------------------------------------------------------------------------


def _paragraph_texts(root: Node) -> Iterator[str]:
    """Each paragraph's own text with its runs joined; a text run outside any
    paragraph is a text of its own."""
    parent: dict[Node, Node] = {c: p for p in root.iter() for c in p}

    def owner(el: Node) -> Node | None:
        node = parent.get(el)
        while node is not None and node.tag != f"{_W}p":
            node = parent.get(node)
        return node

    joined: dict[Node, list[str]] = {}
    loose: list[str] = []
    for t in root.iter(f"{_W}t"):
        p = owner(t)
        if p is None:
            loose.append(t.text or "")
        else:
            joined.setdefault(p, []).append(t.text or "")
    for texts in joined.values():
        yield "".join(texts)
    yield from loose


def _segment_ok(segment: str, fixed: frozenset[str], page_story: bool, used: set[str]) -> bool:
    folded = _fold(segment)
    if not folded or _PUNCT_ONLY.match(folded):
        return True
    if folded in fixed:
        used.add(folded)
        return True
    return bool(page_story and _PAGE_LINE.fullmatch(folded))


def _text_problems(
    name: str, root: Node, fixed: frozenset[str], used: set[str], marks: list[str]
) -> list[str]:
    page_story = bool(_STORY_WITH_PAGES.match(name))
    out: list[str] = []
    for i, text in enumerate(_paragraph_texts(root), start=1):
        marks.extend(m.group(0) for m in _MARK.finditer(text))
        whole = _fold(text)
        if whole in fixed:
            used.add(whole)
            continue
        if not all(_segment_ok(s, fixed, page_story, used) for s in _MARK.split(text)):
            out.append(
                f"{name}: paragraph {i} holds text outside its marked places and fixed strings"
            )
    return out


def _field_problems(name: str, root: Node) -> list[str]:
    codes = [el.get(f"{_W}instr", "") for el in root.iter(f"{_W}fldSimple")]
    current: list[str] = []
    for el in root.iter():
        if el.tag == f"{_W}instrText":
            current.append(el.text or "")
        elif (
            el.tag == f"{_W}fldChar"
            and el.get(f"{_W}fldCharType") in ("separate", "end")
            and current
        ):
            codes.append("".join(current))
            current = []
    if current:
        codes.append("".join(current))
    out = []
    for code in codes:
        word = (code.strip().split() or [""])[0].upper()
        if word not in _FIELDS_ALLOWED:
            out.append(f"{name}: a field other than a page number (a field can carry text)")
    return out


def _element_problems(name: str, root: Node, fixed: frozenset[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    def once(why: str) -> None:
        if why not in seen:
            seen.add(why)
            out.append(f"{name}: {why}")

    for el in root.iter():
        tag = el.tag
        if tag in _REVISIONS:
            once("a tracked change")
        elif tag in _COMMENT_MARKS or tag == f"{_W}comment":
            once("a comment")
        elif _local(tag) in _TEXT_BOX_LOCAL and not _STORY_WITH_PAGES.match(name):
            # A header's or footer's text box (pleading paper numbers its lines
            # in one) is read as paragraphs like the rest of its story and must
            # say nothing but page numbering or a fixed string; elsewhere, refused.
            once("a text box")
        elif tag in (f"{_W}customXml", f"{_W}dataBinding"):
            once("custom XML in the document")
        elif tag in _EMBEDS:
            once("an embedded object")
        elif tag in _SETTINGS:
            once("a document variable, mail merge or attached template")
        elif _local(tag) == "docPr":
            for attr in ("descr", "title"):
                value = _fold(el.get(attr, ""))
                if value and value not in fixed:
                    once("a picture's alternative text")
        elif tag in (f"{_W}alias", f"{_W}tag"):
            value = _fold(el.get(f"{_W}val", ""))
            if value and value not in fixed and not _MARK_NAME.match(value):
                once("a content control's name")
    return out


# ---- properties and relationships ----------------------------------------------------


def _property_problems(z: Package, names: set[str]) -> list[str]:
    out: list[str] = []
    if CORE in names:
        core = _parse(z, CORE, out)
        if core is not None:
            filled = sorted(
                {
                    _local(e.tag)
                    for e in core
                    if _local(e.tag) in _CORE_PEOPLE and _fold(e.text or "")
                }
            )
            out += [f"{CORE}: {k} is not empty (it can name a person or a matter)" for k in filled]
    if CUSTOM in names:
        custom = _parse(z, CUSTOM, out)
        if custom is not None and len(custom):
            out.append(
                f"{CUSTOM}: carries custom properties (a document system's matter and file ids)"
            )
    if APP in names:
        app = _parse(z, APP, out)
        if app is not None:
            for el in app.iter():
                local = _local(el.tag)
                if local == "Manager" and _fold(el.text or ""):
                    out.append(f"{APP}: manager is not empty")
                if local == "TitlesOfParts" and any(
                    _fold(x.text or "") for x in el.iter() if _local(x.tag) == "lpstr"
                ):
                    out.append(f"{APP}: the titles of parts are not empty")
    return out


def _relationship_problems(z: Package, names: set[str]) -> list[str]:
    out: list[str] = []
    for name in sorted(n for n in names if n.endswith(".rels")):
        root = _parse(z, name, out)
        if root is None:
            continue
        if any(r.get("TargetMode") == "External" for r in root.iter(f"{_REL}Relationship")):
            out.append(f"{name}: an external link (a hyperlink, linked picture or template)")
    return out


# ---- the check ------------------------------------------------------------------------


def check(blob: bytes, fixed_strings: Iterable[str] = ()) -> BlankCheck:
    """Every reason ``blob`` is not a blanked form; none when it is one."""
    z, problems = _open(blob)
    if z is None:
        return BlankCheck(tuple(problems))
    fixed = frozenset(f for f in (_fold(s) for s in fixed_strings) if f)
    names = set(z.namelist())
    problems += _main_type_problems(z)
    problems += _structure_problems(names)
    problems += _property_problems(z, names)
    problems += _relationship_problems(z, names)
    used: set[str] = set()
    marks: list[str] = []
    read = 0
    for name in sorted(
        n
        for n in names
        if n.endswith(".xml") and not n.lower().startswith(("docprops/", "customxml/"))
    ):
        root = _parse(z, name, problems)
        if root is None:
            continue
        read += 1
        problems += _element_problems(name, root, fixed)
        problems += _field_problems(name, root)
        problems += _text_problems(name, root, fixed, used, marks)
    return BlankCheck(tuple(problems), tuple(sorted(used)), read, tuple(marks))


def is_word_package(blob: bytes) -> bool:
    """True when ``blob`` opens as a Word document or template package."""
    z, problems = _open(blob)
    return z is not None and not problems and not _main_type_problems(z)


__all__ = ["MAIN_TYPES", "BlankCheck", "check", "is_word_package"]
