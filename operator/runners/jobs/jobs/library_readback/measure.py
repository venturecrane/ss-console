"""What a filed Word document is made of, measured against the file it was made in.

Tests (a) and (d) of the read-back ask one question of two files: does the
filed document carry the look of the file the record says it was made in? A
look is what the firm set in that file and the body never rewrites:

* ``page_size`` and ``page_margins``: the attributes of the body's last section
  properties (``w:pgSz``, ``w:pgMar``);
* ``headers_footers``: every header and footer part the file has, by its
  canonical XML (Word's own part names), each one present in the filed
  document and the same;
* ``styles``: every named style the file defines (``word/styles.xml``), by its
  canonical XML, present in the filed document and the same. The filed
  document may define more (the renderer adds its own when the file lacks
  them); it may not change or drop one of the file's;
* a marked file only (``marked``): ``marks_left``, the marked places still in
  the filed document (a place nothing filled), and ``fixed_missing``, the
  file's fixed words (``library_edit.check.fixed_of``: every word the file
  carries outside its marked places) that the filed document does not carry.

Each difference is a property name and, for a part or a style, its name in the
package (a style id, a part name): never a word of either document. A file
that is not a Word package is a property of its own (``not_a_word_file``).
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Iterable
from typing import Any

from lxml import etree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
DOCUMENT = "word/document.xml"
STYLES = "word/styles.xml"
_STORY = re.compile(r"\Aword/(header|footer)\d*\.xml\Z")
#: A marked place, in either spelling the firm-base code writes.
MARK = re.compile(r"\[\[[a-z0-9._]+\]\]|\{\{[a-z][a-z0-9._]*\}\}")
MARKED = "marked"


def members(blob: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        return {n: z.read(n) for n in z.namelist()}


def _canon(el: Any) -> bytes:
    return etree.tostring(el, method="c14n")


def _body(members: dict[str, bytes]) -> Any:
    return etree.fromstring(members[DOCUMENT]).find(f"{W}body")


def page_setup(members: dict[str, bytes]) -> dict[str, dict[str, str]]:
    """The body's last section's page size and margins, attribute by attribute."""
    body = _body(members)
    sects = [] if body is None else list(body.iter(f"{W}sectPr"))
    if not sects:
        return {"page_size": {}, "page_margins": {}}
    sect = sects[-1]
    out: dict[str, dict[str, str]] = {}
    for name, tag in (("page_size", "pgSz"), ("page_margins", "pgMar")):
        el = sect.find(f"{W}{tag}")
        out[name] = {} if el is None else {etree.QName(k).localname: v for k, v in sorted(el.attrib.items())}
    return out


def stories(members: dict[str, bytes]) -> dict[str, bytes]:
    """Each header and footer part, by name, as canonical XML."""
    return {n: _canon(etree.fromstring(b)) for n, b in members.items() if _STORY.match(n)}


def styles(members: dict[str, bytes], *, without_spacing: bool = False) -> dict[str, bytes]:
    """Each style the package defines, by style id, as canonical XML.
    ``without_spacing``: each style's paragraph spacing set aside, for a document
    a layout rule in force respaced (test (e) measures that rule)."""
    if STYLES not in members:
        return {}
    out: dict[str, bytes] = {}
    for st in etree.fromstring(members[STYLES]).iter(f"{W}style"):
        el = st
        if without_spacing:
            el = etree.fromstring(_canon(st))
            for sp in el.iter(f"{W}spacing"):
                sp.getparent().remove(sp)
        out[st.get(f"{W}styleId", "")] = _canon(el)
    return out


def paragraph_texts(members: dict[str, bytes]) -> list[str]:
    """Every paragraph's text in the body, tables included, in order, whitespace folded."""
    body = _body(members)
    if body is None:
        return []
    out = []
    for p in body.iter(f"{W}p"):
        text = "".join(t.text or "" for t in p.iter(f"{W}t"))
        out.append(re.sub(r"\s+", " ", text).strip())
    return out


def marks_left(members: dict[str, bytes]) -> int:
    """Marked places still in the document's body, headers and footers."""
    n = 0
    for name, blob in members.items():
        if name == DOCUMENT or _STORY.match(name):
            root = etree.fromstring(blob)
            for p in root.iter(f"{W}p"):
                n += len(MARK.findall("".join(t.text or "" for t in p.iter(f"{W}t"))))
    return n


def fixed_missing(base: bytes, filed: dict[str, bytes]) -> int:
    """How many of the file's fixed words the filed document does not carry."""
    from ..library_edit.check import fixed_of

    text = "\n".join(paragraph_texts(filed))
    for name, blob in filed.items():
        if _STORY.match(name):
            root = etree.fromstring(blob)
            text += "\n" + "\n".join("".join(t.text or "" for t in p.iter(f"{W}t")) for p in root.iter(f"{W}p"))
    folded = re.sub(r"[ \t]+", " ", text)
    return sum(1 for piece in fixed_of(base) if piece not in folded)


def _named(base: dict[str, bytes], filed: dict[str, bytes]) -> tuple[list[str], list[str]]:
    missing = sorted(k for k in base if k not in filed)
    changed = sorted(k for k in base if k in filed and base[k] != filed[k])
    return missing, changed


def compare(base: bytes, filed: bytes, contract: str, *, without_spacing: bool = False) -> dict[str, Any]:
    """``{"differs": [property, ...], "measured": {...}}``: how ``filed`` stands
    to ``base``. ``differs`` empty is "made in this file"."""
    try:
        b, f = members(base), members(filed)
        out: dict[str, Any] = {"differs": [], "measured": {}}
        bp, fp = page_setup(b), page_setup(f)
        for prop in ("page_size", "page_margins"):
            if bp[prop] != fp[prop]:
                out["differs"].append(prop)
                out["measured"][prop] = {"file": bp[prop], "filed": fp[prop]}
        miss, changed = _named(stories(b), stories(f))
        out["differs"] += [f"header_footer_missing:{n}" for n in miss] + [f"header_footer_changed:{n}" for n in changed]
        bs, fs = styles(b, without_spacing=without_spacing), styles(f, without_spacing=without_spacing)
        miss, changed = _named(bs, fs)
        out["differs"] += [f"style_missing:{n}" for n in miss] + [f"style_changed:{n}" for n in changed]
        out["measured"].update(styles_in_file=len(bs), styles_in_filed=len(fs), headers_footers=len(stories(b)))
        if contract == MARKED:
            left, gone = marks_left(f), fixed_missing(base, f)
            out["measured"].update(marks_left=left, fixed_missing=gone)
            out["differs"] += (["marks_left"] if left else []) + (["fixed_missing"] if gone else [])
        return out
    except (KeyError, ValueError, zipfile.BadZipFile, etree.XMLSyntaxError):
        return {"differs": ["not_a_word_file"], "measured": {}}


def find_by_sha(sha: str, places: Iterable[Any]) -> bytes | None:
    """The bytes whose sha256 is ``sha``, from the first place that holds them:
    a file path, or a folder searched (two levels) for ``.docx`` files."""
    import hashlib
    from pathlib import Path

    for place in places:
        p = Path(place)
        candidates = [p] if p.is_file() else [*p.glob("*.docx"), *p.glob("*/*.docx")] if p.is_dir() else []
        for c in candidates:
            try:
                data = c.read_bytes()
            except OSError:
                continue
            if hashlib.sha256(data).hexdigest() == sha:
                return data
    return None


__all__ = [
    "MARK",
    "MARKED",
    "compare",
    "find_by_sha",
    "marks_left",
    "members",
    "page_setup",
    "paragraph_texts",
    "styles",
]
