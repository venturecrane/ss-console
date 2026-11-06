"""Marked places read from a paragraph's whole text (firmbase/markers.py).

The fixtures are built in code: a marked file, then the same file with each
marker split across two and three runs, with a proofing mark and a bookmark
between, the way Word leaves a line it has saved. Fixtures saved by Word itself
are still owed; a person with Word makes them.
"""

from __future__ import annotations

import copy
import io

import docx
import pytest
from lxml import etree

from jobs.firmbase import base as fb
from jobs.firmbase import markers
from jobs.firmbase.base import W


def _marked_file() -> bytes:
    d = docx.Document()
    d.add_paragraph("The firm's own fixed line")
    d.add_paragraph("[[who]]")
    d.add_paragraph("[[body.h]]")
    t = d.add_table(rows=2, cols=2)
    for row in t.rows:
        for cell in row.cells:
            cell.paragraphs[0].text = "[[body.t]]"
    d.add_paragraph("[[sign]]")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _body(blob: bytes):
    import zipfile

    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        root = etree.fromstring(z.read(fb.DOCUMENT))
    return root.find(f"{W}body")


def _run(text: str, bold: bool = False):
    r = etree.Element(f"{W}r")
    if bold:
        etree.SubElement(etree.SubElement(r, f"{W}rPr"), f"{W}b")
    t = etree.SubElement(r, f"{W}t")
    t.text = text
    return r


def _split(body, pieces_for: dict[int, int], between: str | None) -> None:
    """Split each marked paragraph's one run into ``n`` runs (by marker length),
    with a ``between`` element (``proofErr`` or ``bookmarkStart``) after the first."""
    for p in body.iter(f"{W}p"):
        texts = [t.text or "" for t in p.iter(f"{W}t")]
        if len(texts) != 1 or not fb.SLOT.match(texts[0]):
            continue
        text = texts[0]
        n = pieces_for.get(len(text), pieces_for.get(0, 2))
        cuts = [round(i * len(text) / n) for i in range(n + 1)]
        parts = [text[a:b] for a, b in zip(cuts, cuts[1:])]
        for r in p.findall(f"{W}r"):
            p.remove(r)
        for i, part in enumerate(parts):
            p.append(_run(part, bold=i % 2 == 1))
            if i == 0 and between:
                mark = etree.SubElement(p, f"{W}{between}")
                mark.set(
                    f"{W}type" if between == "proofErr" else f"{W}id", "spellStart" if between == "proofErr" else "0"
                )


@pytest.mark.parametrize("pieces", [2, 3])
@pytest.mark.parametrize("between", [None, "proofErr", "bookmarkStart"])
def test_a_split_marker_reads_the_same_names_as_the_unsplit_original(pieces, between) -> None:
    original = _body(_marked_file())
    want = fb.slot_names(original)
    assert want == ["who", "body.h", "body.t", "body.t", "body.t", "body.t", "sign"]
    split = copy.deepcopy(original)
    _split(split, {0: pieces}, between)
    assert fb.slot_names(split) != want, "the fixture really is split: the single-run reader misses it"
    assert markers.slot_names(split) == want


def test_every_element_of_an_unsplit_file_reads_as_the_single_run_reader_reads_it() -> None:
    body = _body(_marked_file())
    for el in body:
        assert markers.slot_names(el) == fb.slot_names(el)


def test_a_one_run_marker_beside_other_runs_keeps_the_single_run_readers_answer() -> None:
    p = etree.Element(f"{W}p")
    p.append(_run("[[who]]"))
    p.append(_run(" and more words"))
    assert fb.slot_names(p) == ["who"]
    assert markers.slot_names(p) == ["who"]


def test_a_split_marker_inside_other_words_is_not_a_marked_place() -> None:
    p = etree.Element(f"{W}p")
    for text in ("See [[", "who", "]] here"):
        p.append(_run(text))
    assert markers.slot_names(p) == []


def test_a_text_box_paragraph_reads_its_own_text_only() -> None:
    outer = etree.Element(f"{W}p")
    outer.append(_run("[["))
    box = etree.SubElement(etree.SubElement(etree.SubElement(outer, f"{W}r"), f"{W}txbxContent"), f"{W}p")
    for text in ("[[in", "ner]]"):
        box.append(_run(text))
    outer.append(_run("outer]]"))
    assert markers.paragraph_text(outer) == "[[outer]]"
    assert markers.slot_names(outer) == ["outer", "inner"]
