"""A marker Word split across runs, merged into its first run so the fill can write it.

``markers.read`` has already proven the paragraph holds only structure a
Word-saved fixture shows, and that its whole text is the marker. :func:`collapse`
then leaves the marker in ONE run, the run that holds its first character, and
``base.fill_para`` writes into that run exactly as it writes into a marker that
was never split. So the filled text takes that run's formatting (its ``rPr``) and
sits where that run sits, inside the wrapper that holds it, if any.

What is kept and what is removed, by structure (the fixture that shows each):

* The marker's first run: KEPT, its ``rPr`` and any tab before the marker; its
  text pieces become the one marker text, and a rendered page break in it goes
  (Word's note of where a page last broke, rewritten on its next layout).
* The marker's other runs: REMOVED, with everything in them (a rendered page
  break, ten; a revision id, three and four; a format of their own, five to seven).
* Spelling and grammar marks (``w:proofErr``, one and two): REMOVED. The whole
  paragraph's text is the marker, so each one marked the marker's text.
* Smart tags (``w:smartTag``, fifteen): UNWRAPPED, their runs left in place.
  A smart tag annotated the marker's words; it says nothing of the filled text.
* Bookmarks (``w:bookmarkStart``, eight; ``w:bookmarkEnd``, nine): KEPT, in place
  among what remains, so a cross-reference the firm made still finds its
  bookmark. The filled text is written where the first run was, so a bookmark
  that began or ended inside the marker now begins or ends after the filled text.
* A hyperlink or content control (``w:hyperlink``, eighteen; ``w:sdt``, twenty)
  that holds the marker's first run: KEPT, the filled text written inside it.
  One that held only later pieces of the marker: REMOVED once those pieces are
  gone, unless a bookmark sits in it, which keeps it.
* A field that begins inside the marker (``w:fldChar``, nineteen): REMOVED WHOLE,
  from its begin to its end. Its result was marker text, and a field left behind
  would put its own text back into the filled line when Word updates it.
* Everything outside the marker (runs before it, such as the tabs in eighteen,
  and anything after its last character): untouched.
"""

from __future__ import annotations

from typing import Any

from lxml import etree

from .markers import W, Reading

_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
_WRAPPERS = (f"{W}hyperlink", f"{W}sdt")
_POINTS = (f"{W}bookmarkStart", f"{W}bookmarkEnd")


def _wrappers(run: Any, p: Any) -> list[Any]:
    """The hyperlinks and content controls between a run and its paragraph."""
    out, node = [], run.getparent()
    while node is not None and node is not p:
        if node.tag in _WRAPPERS:
            out.append(node)
        node = node.getparent()
    return out


def _unwrap(el: Any) -> None:
    for child in list(el):
        if child.tag != f"{W}smartTagPr":
            el.addprevious(child)
    el.getparent().remove(el)


def _emptied(wrapper: Any) -> bool:
    return wrapper.find(f".//{W}r") is None and next(wrapper.iter(*_POINTS), None) is None


def collapse(p: Any, reading: Reading) -> Any:
    """The paragraph with its marker in one run, the run that held its first
    character; returns that run (see the module docstring for what goes)."""
    first, rest = reading.runs[0], [*reading.runs[1:], *reading.fields]
    keep = set(_wrappers(first, p))
    candidates = list(dict.fromkeys(w for r in rest for w in _wrappers(r, p) if w not in keep))
    for r in rest:
        r.getparent().remove(r)
    for c in list(first):
        if c.tag in (f"{W}t", f"{W}lastRenderedPageBreak"):
            first.remove(c)
    t = etree.SubElement(first, f"{W}t")
    t.text = f"[[{reading.names[0]}]]"
    t.set(_XML_SPACE, "preserve")
    for mark in list(p.iter(f"{W}proofErr")):
        mark.getparent().remove(mark)
    for tag in list(p.iter(f"{W}smartTag")):
        _unwrap(tag)
    for wrapper in candidates:
        if wrapper.getparent() is not None and _emptied(wrapper):
            wrapper.getparent().remove(wrapper)
    return first


__all__ = ["collapse"]
