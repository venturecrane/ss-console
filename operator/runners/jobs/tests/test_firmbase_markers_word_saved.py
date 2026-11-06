"""Marked places read from run structure saved by Word itself (firmbase/markers.py).

``test_firmbase_markers.py`` proves the joined-run reader on fixtures built in
code. These fixtures are the other half: each is one paragraph whose runs were
cut by Word when a person saved a document (a proofing mark, a revision id, a
format change on part of the line, a bookmark, a rendered page break, a tab, a
tracked change, a hyperlink, a field), with every character of text replaced
by a marker and every identifier replaced by a fixed made-up value. See
``fixtures/word_saved_markers/README.md``.

The scrub check runs here too, over the whole fixture directory, so a fixture
holding anything that is not on its closed lists cannot be committed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

import firmbase_word_saved_scrub as scrub
from jobs.firmbase import base as fb
from jobs.firmbase import markers

FIXTURES = Path(__file__).parent / "fixtures" / "word_saved_markers"
FILES = scrub.fixture_files(FIXTURES)

# Every way Word was seen to cut a line; each must keep at least one fixture.
CAUSES = (
    "proof-mark-spelling",
    "proof-mark-grammar",
    "revision-id-run",
    "revision-id-format",
    "bold-on-part",
    "underline-on-part",
    "superscript-on-part",
    "bookmark-start",
    "bookmark-end",
    "rendered-page-break",
    "tab-in-line",
    "line-break-in-line",
    "two-texts-one-run",
    "no-break-hyphen",
    "smart-tag-on-part",
    "tracked-insertion",
    "tracked-deletion",
    "hyperlink-on-part",
    "field-on-part",
    "content-control-on-part",
    "identical-runs",
)


def _ids(paths: list[Path]) -> list[str]:
    return [p.stem for p in paths]


def test_every_cause_has_a_fixture_and_the_directory_holds_nothing_else() -> None:
    assert len(FILES) >= 12
    labels = {p.stem.split("-", 1)[1] for p in FILES}
    assert labels == set(CAUSES)
    assert sorted(p.name for p in FIXTURES.iterdir()) == sorted(["README.md", *(p.name for p in FILES)])


@pytest.mark.parametrize("path", FILES, ids=_ids(FILES))
def test_a_word_saved_fixture_is_scrubbed(path: Path) -> None:
    assert scrub.problems(path.name, path.read_bytes()) == []


@pytest.mark.parametrize("path", FILES, ids=_ids(FILES))
def test_a_marker_cut_by_word_is_read_from_the_joined_runs(path: Path) -> None:
    marker = scrub.marker_for(path.name)
    assert marker is not None
    paragraph = etree.fromstring(path.read_bytes())
    assert fb.slot_names(paragraph) == [], "the fixture really is split: the single-run reader misses it"
    assert markers.paragraph_text(paragraph) == marker
    assert markers.slot_names(paragraph) == [marker[2:-2]]


# The scrub check can fail: each change below dirties a clean fixture one way.
DIRTY = {
    "a word in a text piece": ("one-proof-mark-spelling.xml", b"<w:t>ture.one]]</w:t>", b"<w:t>ture.one]] kept</w:t>"),
    "a word in place of a text piece": ("five-bold-on-part.xml", b"<w:t>[[fi</w:t>", b"<w:t>Dear</w:t>"),
    "a real-looking revision id": ("three-revision-id-run.xml", b'w:rsidR="00A00004"', b'w:rsidR="00B4172C"'),
    "an author": ("sixteen-tracked-insertion.xml", b'w:author="Fixture Author"', b'w:author="Jane Example"'),
    "a date": ("sixteen-tracked-insertion.xml", b'w:date="2000-01-01T00:00:00Z"', b'w:date="2026-11-05T09:14:00Z"'),
    "a deleted word": ("seventeen-tracked-deletion.xml", b"<w:delText>###</w:delText>", b"<w:delText>was</w:delText>"),
    "a font": ("eleven-tab-in-line.xml", b'w:ascii="Fixture Sans"', b'w:ascii="Garamond"'),
    "a style": ("twenty-content-control-on-part.xml", b'w:val="Heading1"', b'w:val="CustomStyle"'),
    "a bookmark name": ("eight-bookmark-start.xml", b'w:name="bm1"', b'w:name="_Hlk0000001"'),
    "a link target": ("eighteen-hyperlink-on-part.xml", b'r:id="rId1"', b'r:id="rId1" w:tooltip="example"'),
    "a field instruction": ("nineteen-field-on-part.xml", b" REF bm1 ", b" MERGEFIELD Example "),
    "a comment": ("two-proof-mark-grammar.xml", b"<w:pPr>", b"<!-- saved by someone --><w:pPr>"),
    "an element on no list": ("two-proof-mark-grammar.xml", b'<w:proofErr w:type="gramEnd"/>', b"<w:customXml/>"),
    "text between runs": ("ten-rendered-page-break.xml", b"<w:lastRenderedPageBreak/>", b"<w:lastRenderedPageBreak/>x"),
    "a marker in one piece": ("five-bold-on-part.xml", b"<w:t>[[fi</w:t>", b"<w:t>[[fixture.five]]</w:t>"),
}


@pytest.mark.parametrize("what", DIRTY)
def test_the_scrub_check_fails_a_dirty_copy(what: str) -> None:
    name, clean, dirty = DIRTY[what]
    blob = (FIXTURES / name).read_bytes()
    assert scrub.problems(name, blob) == []
    assert clean in blob, "the change really lands"
    assert scrub.problems(name, blob.replace(clean, dirty)) != []


@pytest.mark.parametrize("name", ["one-proof-mark-spelling.docx", "one-example-words.xml", "first-bold-on-part.xml"])
def test_the_scrub_check_fails_a_file_name_off_its_lists(name: str) -> None:
    blob = (FIXTURES / "one-proof-mark-spelling.xml").read_bytes()
    assert scrub.problems(name, blob) != []
