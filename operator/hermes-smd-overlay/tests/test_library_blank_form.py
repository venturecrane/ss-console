"""The no-client-text check on an accepted file's bytes (establish_intake/blank_form.py).

A blanked form passes; each planted leak fails, and the reason names the place,
never the planted words. Every fixture is built in code with made-up text.
"""

from __future__ import annotations

import pytest

from establish_intake import blank_form
from tests.library_fixtures import FIXED, FORM_BODY, W_NS, docx, p

LEAK = "Jordan Placeholder slipped on the stairs at Example Mart"


def _problems(blob: bytes, fixed=FIXED) -> tuple[str, ...]:
    return blank_form.check(blob, fixed).problems


def test_a_blanked_marked_form_passes() -> None:
    result = blank_form.check(docx(), FIXED)
    assert result.ok, result.problems
    assert "[[party.defendant]]" in result.marks  # a marker Word split across three runs
    assert "IN THE SUPERIOR COURT OF THE STATE OF EXAMPLE" in result.fixed_used


def test_a_styles_file_with_an_empty_body_passes_with_no_fixed_strings() -> None:
    assert blank_form.check(docx(p("")), ()).ok


def test_a_stray_sentence_in_the_body_fails() -> None:
    problems = _problems(docx(FORM_BODY + p(LEAK)))
    assert any("word/document.xml: paragraph" in x for x in problems)
    assert not any("Jordan" in x for x in problems)


def test_client_text_beside_a_marked_place_fails() -> None:
    assert _problems(docx(p("[[party.plaintiff]] ", "Jordan Placeholder")))


def test_a_fixed_string_is_matched_whole_not_as_a_prefix() -> None:
    assert _problems(docx(p("Plaintiff, Jordan Placeholder")))


def test_a_tracked_change_fails() -> None:
    body = (
        FORM_BODY
        + f'<w:p><w:ins w:id="1" w:author="Pat Example"><w:r><w:t>{"[[x]]"}</w:t></w:r></w:ins></w:p>'
    )
    assert any("a tracked change" in x for x in _problems(docx(body)))


def test_a_deletion_fails_even_when_its_text_is_hidden() -> None:
    body = FORM_BODY + f"<w:p><w:del><w:r><w:delText>{LEAK}</w:delText></w:r></w:del></w:p>"
    assert any("a tracked change" in x for x in _problems(docx(body)))


def test_a_comment_fails() -> None:
    comments = (
        f'<?xml version="1.0"?><w:comments xmlns:w="{W_NS}"><w:comment w:id="0" w:author="Pat Example">'
        "<w:p><w:r><w:t>call the client</w:t></w:r></w:p></w:comment></w:comments>"
    )
    body = FORM_BODY + '<w:p><w:commentRangeStart w:id="0"/><w:r><w:t>[[x]]</w:t></w:r></w:p>'
    problems = _problems(docx(body, extra_parts={"word/comments.xml": comments}))
    assert any("a comment" in x for x in problems)


def test_a_text_box_fails() -> None:
    body = (
        FORM_BODY
        + "<w:p><w:r><w:pict><w:txbxContent>"
        + p("[[x]]")
        + "</w:txbxContent></w:pict></w:r></w:p>"
    )
    assert any("a text box" in x for x in _problems(docx(body)))


def _boxed_footer(*lines: str) -> str:
    return (
        "<w:p><w:r><w:pict><w:txbxContent>"
        + "".join(p(x) for x in lines)
        + "</w:txbxContent></w:pict></w:r></w:p>"
    )


def test_pleading_papers_line_numbers_in_a_footer_text_box_pass() -> None:
    assert blank_form.check(docx(footer=_boxed_footer("1", "2", "3", "28")), FIXED).ok


def test_a_name_in_a_footer_text_box_still_fails() -> None:
    problems = _problems(docx(footer=_boxed_footer("1", "Jordan Placeholder")))
    assert any("word/footer1.xml: paragraph" in x for x in problems)
    assert not any("Jordan" in x for x in problems)


def test_a_custom_xml_part_fails() -> None:
    problems = _problems(
        docx(extra_parts={"customXml/item1.xml": "<matter><id>2026-0001</id></matter>"})
    )
    assert any("a custom XML part" in x for x in problems)


def test_a_name_in_the_document_properties_fails() -> None:
    problems = _problems(docx(creator="Pat Example"))
    assert any("docProps/core.xml: creator is not empty" in x for x in problems)
    assert not any("Pat" in x for x in problems)


def test_a_custom_property_fails() -> None:
    custom = (
        '<?xml version="1.0"?><Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties">'
        '<property name="MatterId"><vt:lpwstr xmlns:vt="x">m-1</vt:lpwstr></property></Properties>'
    )
    assert any(
        "custom properties" in x
        for x in _problems(docx(extra_parts={"docProps/custom.xml": custom}))
    )


def test_a_field_that_can_carry_text_fails() -> None:
    body = (
        FORM_BODY
        + '<w:p><w:fldSimple w:instr=" HYPERLINK mailto:someone@example.com "><w:r><w:t>[[x]]</w:t></w:r></w:fldSimple></w:p>'
    )
    assert any("a field other than a page number" in x for x in _problems(docx(body)))


def test_an_external_link_fails() -> None:
    rels = (
        '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId9" Type="hyperlink" Target="https://example.com/matter" TargetMode="External"/>'
        "</Relationships>"
    )
    assert any(
        "an external link" in x
        for x in _problems(docx(extra_parts={"word/_rels/document.xml.rels": rels}))
    )


def test_text_in_a_header_that_is_not_page_numbering_fails() -> None:
    assert _problems(docx(footer=p("Re: Jordan Placeholder")))


def test_page_numbering_outside_a_header_or_footer_is_text() -> None:
    assert _problems(docx(p("Page 4 of 9")))


def test_a_macro_enabled_document_is_refused() -> None:
    blob = docx(main_type="application/vnd.ms-word.document.macroEnabled.main+xml")
    assert not blank_form.is_word_package(blob)
    assert _problems(blob)


@pytest.mark.parametrize("blob", [b"", b"not a zip", docx()[:200]])
def test_bytes_that_are_not_a_word_package_fail(blob: bytes) -> None:
    assert not blank_form.is_word_package(blob)
    assert _problems(blob)


def test_alt_text_on_a_picture_fails() -> None:
    body = (
        FORM_BODY
        + '<w:p><w:r><w:drawing><wp:docPr xmlns:wp="x" id="1" name="Picture 1" descr="photo of the scene"/></w:drawing></w:r></w:p>'
    )
    assert any("alternative text" in x for x in _problems(docx(body)))
