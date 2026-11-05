"""The proof of a revision: a leaf-level diff of the filed package against the
revised one, every part, canonical.

``parts_differ`` reads every part of both files. Content types and
relationships compare as sets, because their order carries no meaning and Word
and python-docx write them differently; other XML compares by c14n with rsid
noise aside; anything else compares by its bytes.

``leaves`` names each place two elements differ, as a path of tags and indexes
among same-named siblings ("tbl/tr[8]/tc[0]/tcPr[0]/tcBorders[0]/bottom[0]@").
A path ending ``@`` is the element's attributes or text. ``Allow`` holds what
one ask may change: an element it touched is proven against the leaves that ask
names, never trusted whole.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any

from lxml import etree

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
#: Added or removed as a whole; what they carry is not a further difference.
_WHOLE = {"r", "tr", "p"}


class Allow:
    """The leaf paths each kind of edit may change."""

    RUNS = re.compile(r"^p/(r|hyperlink|ins|del|proofErr)\[\d+\]")
    HEADING_LEVEL = re.compile(r"^p/pPr\[0\]")
    SPACING = re.compile(r"^p/pPr\[0\](/spacing\[0\]@?)?$")
    TABLE_TEXT = re.compile(r"^tbl/tr\[\d+\](/tc\[\d+\]/p\[\d+\](/(r|hyperlink|ins|del|proofErr)\[\d+\].*)?)?$")
    CHART_FONT = re.compile(r"^tbl/tr\[\d+\]/tc\[\d+\]/p\[\d+\]/r\[\d+\]/rPr\[0\](/(sz|szCs)\[0\]@?)?$")
    CHART_FILL = re.compile(r"^tbl/tr\[\d+\]/tc\[\d+\]/tcPr\[0\](/shd\[0\]@?)?$")
    CHART_BORDERS = re.compile(r"^tbl/tblPr\[0\]/tblBorders\[0\](/\w+\[0\]@?)?@?$")
    ANYWHERE = re.compile(r"")

    @staticmethod
    def caption_rule(last_row: int) -> re.Pattern[str]:
        return re.compile(
            r"^tbl/tblPr\[0\]/tblBorders\[0\](/bottom\[0\]@?)?$"
            rf"|^tbl/tr\[{last_row}\]/tc\[\d+\]/tcPr\[0\](/tcBorders\[0\](/bottom\[0\]@?)?)?$"
        )


def _strip(el: Any) -> Any:
    out = etree.fromstring(etree.tostring(el))
    for x in out.iter():
        if isinstance(x.tag, str):
            for k in [k for k in x.attrib if "rsid" in k or k.endswith("}paraId") or k.endswith("}textId")]:
                del x.attrib[k]
    return out


def canon(el: Any) -> bytes:
    return etree.tostring(_strip(el), method="c14n")


def _local(el: Any) -> str:
    return etree.QName(el).localname


def _elements(el: Any) -> list[Any]:
    return [c for c in el if isinstance(c.tag, str)]


def _subtree(el: Any, path: str, out: list[str]) -> None:
    out.append(path)
    if _local(el) in _WHOLE:
        return
    seen: dict[str, int] = {}
    for c in _elements(el):
        tag = _local(c)
        _subtree(c, f"{path}/{tag}[{seen.get(tag, 0)}]", out)
        seen[tag] = seen.get(tag, 0) + 1


def _plain(el: Any) -> tuple[Any, str]:
    return dict(_strip(el).attrib), el.text or ""


def _walk(a: Any, b: Any, path: str, out: list[str]) -> None:
    if _plain(a) != _plain(b):
        out.append(f"{path}@")
    ka, kb = _elements(a), _elements(b)
    tags = list(dict.fromkeys(_local(c) for c in [*ka, *kb]))
    for tag in tags:
        xs, ys = [c for c in ka if _local(c) == tag], [c for c in kb if _local(c) == tag]
        for i, (x, y) in enumerate(zip(xs, ys)):
            if canon(x) != canon(y):
                _walk(x, y, f"{path}/{tag}[{i}]", out)
        for i, extra in enumerate(_extra(xs, ys)):
            _subtree(extra, f"{path}/{tag}[{min(len(xs), len(ys)) + i}]", out)
    if _order(ka, kb) != _order(kb, ka):
        out.append(f"{path}/order")


def _extra(xs: list[Any], ys: list[Any]) -> list[Any]:
    n = min(len(xs), len(ys))
    return xs[n:] + ys[n:]


def _order(ka: list[Any], kb: list[Any]) -> list[tuple[str, int]]:
    """``ka``'s children as (tag, nth of that tag), those both sides have."""
    have: dict[str, int] = {}
    for c in kb:
        have[_local(c)] = have.get(_local(c), 0) + 1
    seen: dict[str, int] = {}
    out = []
    for c in ka:
        tag = _local(c)
        n = seen.get(tag, 0)
        seen[tag] = n + 1
        if n < have.get(tag, 0):
            out.append((tag, n))
    return out


def leaves(a: Any, b: Any) -> list[str]:
    """Where two elements differ, as leaf paths; empty when they do not."""
    out: list[str] = []
    if canon(a) != canon(b):
        _walk(a, b, _local(a), out)
    return out


def _listing(root: Any) -> list[tuple[Any, ...]]:
    return sorted((_local(c), tuple(sorted(c.attrib.items()))) for c in _elements(root))


def _part(name: str, data: bytes) -> Any:
    if name == "[Content_Types].xml" or name.endswith(".rels"):
        return _listing(etree.fromstring(data))
    if name.endswith(".xml"):
        return canon(etree.fromstring(data))
    return data


def parts_differ(filed: bytes, revised: bytes) -> list[str]:
    """Every part but the body that is not the same in both packages. The
    document part is the root's attributes and what sits beside the body."""
    a, b = zipfile.ZipFile(io.BytesIO(filed)), zipfile.ZipFile(io.BytesIO(revised))
    out = []
    for name in sorted(set(a.namelist()) | set(b.namelist())):
        if name in set(a.namelist()) ^ set(b.namelist()):
            out.append(name)
        elif name == "word/document.xml":
            if _beside_body(a.read(name)) != _beside_body(b.read(name)):
                out.append(name)
        elif _part(name, a.read(name)) != _part(name, b.read(name)):
            out.append(name)
    return out


def _beside_body(data: bytes) -> tuple[Any, ...]:
    root = etree.fromstring(data)
    return dict(root.attrib), root.nsmap, [canon(c) for c in _elements(root) if c.tag != f"{_W}body"]


def body_of(blob: bytes) -> list[Any]:
    """The body's top-level elements, parsed from the package itself."""
    root = etree.fromstring(zipfile.ZipFile(io.BytesIO(blob)).read("word/document.xml"))
    return _elements(root.find(f"{_W}body"))


def prove_body(ed: Any, before: dict[Any, str], filed: bytes, revised: bytes) -> list[str]:
    """Each element of the revised body against the same element as filed:
    every leaf that differs must be one its ask names, every new element one an
    edit made, and every element gone one an edit removed."""
    was, now = body_of(filed), body_of(revised)
    place = {el: i for i, el in enumerate(before)}
    live = list(ed.doc.element.body)
    if len(now) != len(live):
        return [f"the saved body has {len(now)} elements, the edit made {len(live)}"]
    out: list[str] = []
    for el, saved in zip(live, now):
        if el not in place:
            if el not in ed.added:
                out.append(f"an element no ask made appeared ({_local(saved)})")
            continue
        allowed = ed.allowed.get(el, [])
        for leaf in leaves(was[place[el]], saved):
            if not any(p.search(leaf) for p in allowed):
                out.append(f"a part of the brief no ask names changed: {leaf}")
                break
    gone = sum(1 for el in before if el not in set(live))
    if gone != ed.removed:
        out.append(f"{gone} element(s) are gone from the brief, and the edit removed {ed.removed}")
    return out
