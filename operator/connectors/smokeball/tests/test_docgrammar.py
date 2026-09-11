"""docgrammar: structure only, markers verbatim through every split.

The load-bearing cases are the ones a naive parser gets wrong in a way that
reads as a FACT in the rendered document (Pattern B): a marker containing a
pipe inside a table cell, a marker inside an emphasis span, a pipe inside a
marker on a prose line.
"""

from __future__ import annotations

import pytest

from smokeball_connector.docgrammar import (
    Bullet,
    Heading,
    HRule,
    Numbered,
    Paragraph,
    Table,
    inline_runs,
    parse_document,
)


def _texts(runs):
    return [r.text for r in runs]


def test_marker_with_pipe_inside_a_table_cell_survives_intact() -> None:
    md = "| Set served | {{FILL: date | proof of service on the set}} |\n| --- | --- |\n| Method | {{FILL: method | proof of service}} |"
    (table,) = parse_document(md)
    assert isinstance(table, Table)
    assert table.header is True
    assert len(table.rows) == 2
    assert [len(row) for row in table.rows] == [2, 2]
    cell = table.rows[0][1]
    assert len(cell) == 1 and cell[0].marker
    assert cell[0].text == "{{FILL: date | proof of service on the set}}"


def test_a_pipe_inside_a_marker_does_not_make_prose_a_table_row() -> None:
    md = "Served on {{FILL: date | proof of service}} by mail."
    (para,) = parse_document(md)
    assert isinstance(para, Paragraph)
    assert para.text == md


def test_emphasis_spans_a_marker_and_the_marker_stays_unstyled() -> None:
    runs = inline_runs("**REQUEST FOR PRODUCTION NO. {{FILL: number | propounded set}}:**")
    assert _texts(runs) == [
        "REQUEST FOR PRODUCTION NO. ",
        "{{FILL: number | propounded set}}",
        ":",
    ]
    assert runs[0].bold and runs[2].bold
    assert runs[1].marker and not runs[1].bold


def test_markers_are_never_split_by_emphasis_characters_inside_them() -> None:
    runs = inline_runs("before {{FILL: *odd* **text** | source}} after")
    assert _texts(runs) == ["before ", "{{FILL: *odd* **text** | source}}", " after"]
    assert runs[1].marker


@pytest.mark.parametrize(
    ("line", "kind", "level"),
    [("# Title", Heading, 1), ("## I. Introduction", Heading, 2), ("### A. Facts", Heading, 3)],
)
def test_headings_keep_their_authored_numerals(line: str, kind, level: int) -> None:
    (block,) = parse_document(line)
    assert isinstance(block, kind)
    assert block.level == level
    assert block.text == line.lstrip("# ")


def test_bullets_numbered_and_rules() -> None:
    blocks = parse_document("- one\n* two\n1. first\n2) second\n---\n")
    assert [type(b) for b in blocks] == [Bullet, Bullet, Numbered, Numbered, HRule]
    assert blocks[2].label == "1." and blocks[3].label == "2)"


def test_four_or_deeper_hashes_and_unknown_syntax_degrade_to_paragraphs() -> None:
    blocks = parse_document("#### too deep\n> quote")
    assert all(isinstance(b, Paragraph) for b in blocks)
    assert [b.text for b in blocks] == ["#### too deep", "> quote"]


def test_code_spans_render_as_plain_text_without_backticks() -> None:
    """The shipped skeletons wrap markers in backticks for human readers; a Word
    document must not carry them, and the marker inside stays a marker."""
    runs = inline_runs("Dated: `{{FILL: date | service date}}` and `plain`.")
    assert _texts(runs) == ["Dated: ", "{{FILL: date | service date}}", " and ", "plain", "."]
    assert runs[1].marker and not runs[3].marker and not runs[3].bold
    (table,) = parse_document("| Set served | `{{FILL: date | proof of service}}` |")
    assert table.rows[0][1][0].text == "{{FILL: date | proof of service}}"


def test_blank_lines_separate_and_are_dropped() -> None:
    blocks = parse_document("para one\n\n\npara two")
    assert [b.text for b in blocks] == ["para one", "para two"]


def test_table_without_separator_has_no_header_row() -> None:
    (table,) = parse_document("| a | b |\n| c | d |")
    assert table.header is False
    assert len(table.rows) == 2


def test_bold_and_italic_runs_inside_cells() -> None:
    (table,) = parse_document("| **Plaintiff**, | *v.* |")
    (row,) = table.rows
    assert row[0][0].bold and row[0][0].text == "Plaintiff"
    assert row[1][0].italic and row[1][0].text == "v."
