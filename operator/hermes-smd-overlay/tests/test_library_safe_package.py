"""Opening an untrusted Word package as root (establish_intake/safe_package.py).

A part that declares a DTD or an entity is refused, never expanded or fetched;
every zip bound is checked on the directory, and the tests prove no member is
read before the refusal by making any read fail the test.
"""

from __future__ import annotations

import io
import random
import zipfile

import pytest

from establish_intake import blank_form, safe_package
from establish_intake.safe_package import PackageRefused, open_package, parse_xml
from tests.library_fixtures import FIXED, W_NS, docx

BOMB = (
    b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
    b'<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]><r>&lol2;</r>'
)
EXTERNAL = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r>&x;</r>'
DTD_ONLY = b'<?xml version="1.0"?><!DOCTYPE r SYSTEM "http://example.com/r.dtd"><r/>'


@pytest.mark.parametrize(
    "xml", [BOMB, EXTERNAL, DTD_ONLY], ids=["entity-bomb", "external-entity", "external-dtd"]
)
def test_a_part_that_declares_a_dtd_or_an_entity_is_refused(xml: bytes) -> None:
    with pytest.raises(PackageRefused, match="declares a DTD or an entity"):
        parse_xml(xml)


def test_an_undeclared_entity_reference_is_refused() -> None:
    with pytest.raises(PackageRefused, match="not well-formed"):
        parse_xml(b"<r>&nope;</r>")


def test_plain_xml_parses_with_namespaces_and_the_five_entities() -> None:
    root = parse_xml(f'<w:p xmlns:w="{W_NS}"><w:t a="1">x &amp; y</w:t></w:p>'.encode())
    (t,) = list(root.iter(f"{{{W_NS}}}t"))
    assert t.text == "x & y" and t.get("a") == "1"


def test_the_check_refuses_a_form_whose_document_declares_an_entity() -> None:
    body_doc = (
        f'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY who "Jordan Placeholder">]>'
        f'<w:document xmlns:w="{W_NS}"><w:body><w:p><w:r><w:t>&who;</w:t></w:r></w:p></w:body></w:document>'
    )
    problems = blank_form.check(docx(extra_parts={"word/document.xml": body_doc}), FIXED).problems
    assert any("word/document.xml: the part declares a DTD or an entity" in p for p in problems)


def test_a_package_whose_content_types_declare_a_dtd_is_not_a_word_package() -> None:
    types = '<?xml version="1.0"?><!DOCTYPE Types SYSTEM "http://example.com/t.dtd"><Types/>'
    assert not blank_form.is_word_package(docx(extra_parts={"[Content_Types].xml": types}))


# ---- the zip bounds, read on the directory alone -------------------------------------


@pytest.fixture
def no_member_read(monkeypatch):
    """Call with a built package: from then on any member read fails the test."""

    def refuse(*_a, **_k):
        raise AssertionError("a member was read before the bounds refused the package")

    def arm(blob: bytes) -> bytes:
        monkeypatch.setattr(zipfile.ZipFile, "read", refuse)
        monkeypatch.setattr(zipfile.ZipFile, "open", refuse)
        return blob

    return arm


def _zip(members: dict[str, bytes], method: int = zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", method) as z:
        for name, data in members.items():
            z.writestr(zipfile.ZipInfo(name), data, compress_type=method)
    return buf.getvalue()


def _refused(blob: bytes, why: str) -> None:
    with pytest.raises(PackageRefused, match=why):
        open_package(blob)


def test_too_many_members_is_refused(no_member_read) -> None:
    _refused(
        no_member_read(_zip({f"p{i}.xml": b"<r/>" for i in range(safe_package.MAX_MEMBERS + 1)})),
        "parts, over",
    )


def test_a_member_that_expands_past_its_ceiling_is_refused(no_member_read) -> None:
    _refused(
        no_member_read(_zip({"word/document.xml": bytes(safe_package.MAX_MEMBER_BYTES + 1)})),
        "expands to",
    )


def test_a_package_that_expands_past_its_total_is_refused(no_member_read) -> None:
    rng = random.Random(7)
    member = rng.randbytes(40_000) + bytes(
        3_200_000
    )  # about 78 to one: under the ratio, near the ceiling
    _refused(
        no_member_read(_zip({f"word/media/image{i}.bin": member for i in range(4)})),
        "the package expands to",
    )


def test_a_zip_bomb_ratio_is_refused(no_member_read) -> None:
    _refused(
        no_member_read(_zip({"word/document.xml": bytes(2 * 1024 * 1024)})), "compresses more than"
    )


@pytest.mark.parametrize("name", ["/etc/cron.d/x", "\\abs", "C:/x", "word/../../x", "../x"])
def test_an_absolute_or_climbing_member_name_is_refused(no_member_read, name: str) -> None:
    _refused(
        no_member_read(_zip({name: b"<r/>", "word/document.xml": b"<r/>"})), "absolute|climbs out"
    )


def test_a_package_too_large_to_be_a_form_is_refused(no_member_read) -> None:
    _refused(no_member_read(b"PK" + bytes(safe_package.MAX_PACKAGE_BYTES)), "over")


def test_a_bounded_form_opens_and_reads() -> None:
    pkg = open_package(docx())
    assert "word/document.xml" in pkg.names
    assert parse_xml(pkg.read("word/document.xml")).tag == f"{{{W_NS}}}document"
