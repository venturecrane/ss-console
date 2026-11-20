"""The checked-box protocol: the pixel read on real renders of the official
form, the strict reader-reply parser, and the three-source reconciliation."""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pytest

import forms_tool
from jobs.drafting.forms import catalog, checkbox, reader

build_catalog = forms_tool.build_catalog()
EM = chr(0x2014)


@pytest.fixture(scope="module")
def blank() -> dict[int, np.ndarray]:
    """The official form's own pages (every box empty), rendered at 150 dpi."""
    return dict(enumerate(checkbox.render_pages(build_catalog.SOURCE.read_bytes(), dpi=150)))


def _mark(pages: dict[int, np.ndarray], keys: Iterable[str]) -> dict[int, np.ndarray]:
    """An X drawn in each named box's checkbox, as a print of a filled form shows it."""
    out = {p: a.copy() for p, a in pages.items()}
    geo = checkbox.geometry()
    for k in keys:
        b = geo[k]
        img = out[b.page]
        s = img.shape[1] / 612.0
        x0, y0, x1, y1 = (int(round(v * s)) for v in b.widget)
        for t in range(x1 - x0):
            y = y0 + t * (y1 - y0) // (x1 - x0)
            for dx in (0, 1):
                img[y, x0 + t + dx] = 0
                img[y1 - 1 - (y - y0), x0 + t + dx] = 0
    return out


def _shift(pages: dict[int, np.ndarray], dx: int, dy: int) -> dict[int, np.ndarray]:
    return {p: np.pad(a, ((dy, 0), (dx, 0)), constant_values=255)[: a.shape[0], : a.shape[1]] for p, a in pages.items()}


def test_blank_official_form_reads_nothing_checked(blank: dict[int, np.ndarray]) -> None:
    r = checkbox.pixel_read(blank)
    assert r.unreadable_pages == ()
    assert len(r.ratios) == 90  # 88 interrogatories and the two Sec. 4(a) boxes
    assert r.checked() == frozenset()
    assert max(r.ratios.values()) < 0.01


def test_marked_boxes_read_as_checked_through_an_offset(blank: dict[int, np.ndarray]) -> None:
    want = {"1.1", "2.1", "6.4", "6.7", "12.5", "16.10", "20.11", "50.6", "4(a)(1)"}
    r = checkbox.pixel_read(_shift(_mark(blank, want), 3, 4))
    assert r.unreadable_pages == ()
    assert r.checked() == want


def test_a_rotated_or_skewed_page_is_unreadable_never_guessed(blank: dict[int, np.ndarray]) -> None:
    from PIL import Image

    marked = _mark(blank, {"6.4"})
    rotated = {p: np.rot90(a).copy() for p, a in marked.items()}
    assert checkbox.pixel_read(rotated).unreadable_pages == tuple(range(8))
    skewed = {p: np.asarray(Image.fromarray(a).rotate(2, fillcolor=255)) for p, a in marked.items()}
    r = checkbox.pixel_read(skewed)
    assert r.unreadable_pages == tuple(range(8)) and r.checked() == frozenset()


def test_a_missing_page_is_unreadable_and_only_its_boxes_drop(blank: dict[int, np.ndarray]) -> None:
    pages = dict(blank)
    del pages[3]
    r = checkbox.pixel_read(pages)
    assert r.unreadable_pages == (3,)
    assert "6.4" not in r.registered and "2.1" in r.registered


def test_form_page_from_the_text_layer() -> None:
    assert checkbox.form_page("DISC-001 ... Page 3 of 8") == 2
    assert checkbox.form_page("PROOF OF SERVICE Page 1 of 2") is None
    assert checkbox.form_page("") is None
    assert checkbox.form_page("DISC-001 Page 9 of 8") is None


def test_form_pages_of_a_served_pdf_with_extra_pages() -> None:
    import pymupdf

    doc = pymupdf.open(stream=build_catalog.SOURCE.read_bytes(), filetype="pdf")
    out = pymupdf.open()
    out.new_page()  # a cover page with no form page number
    out.insert_pdf(doc)
    pages = checkbox.form_pages(out.tobytes(), dpi=72)
    assert sorted(pages) == list(range(8))


def test_map_form_pages_keys_images_by_the_page_each_text_names() -> None:
    img = np.full((10, 8), 255, dtype=np.uint8)
    texts = ["PROOF OF SERVICE", "DISC-001 Page 2 of 8", "DISC-001 Page 1 of 8", "DISC-001 Page 2 of 8"]
    pages = checkbox.map_form_pages(texts, [img, img * 0, img, img])
    assert sorted(pages) == [0, 1]
    assert pages[1].max() == 0  # the first image naming page 2 wins
    with pytest.raises(ValueError):
        checkbox.map_form_pages(texts, [img])


# ---- the reader reply -------------------------------------------------------------


def _reply(checked: str, definition: str = "4(a)(1)", additions: str = "NONE", unreadable: str = "NONE") -> str:
    return f"CHECKED:\n{checked}\nDEFINITION:\n{definition}\nADDITIONS:\n{additions}\nUNREADABLE:\n{unreadable}\n"


def test_reader_instruction_carries_the_measured_text() -> None:
    assert reader.READER_INSTRUCTION.startswith("List every interrogatory number whose box is checked on this form")
    assert "List nothing that is not checked." in reader.READER_INSTRUCTION
    assert EM not in reader.READER_INSTRUCTION


def test_parse_reply_accepts_the_shape() -> None:
    r = reader.parse_reply(_reply("1.1\n2.1\n6.4", unreadable="page 4: a smudge beside 8.2"))
    assert r.checked == ("1.1", "2.1", "6.4") and r.definition == ("4(a)(1)",)
    assert r.additions == () and r.unreadable == ("page 4: a smudge beside 8.2",)


@pytest.mark.parametrize(
    "reply",
    [
        _reply("1.1\n5.1"),  # not on the form
        _reply("6.4\n2.1"),  # out of order
        _reply("2.1\n2.1"),  # twice
        _reply("2.1\nNONE"),  # NONE not alone
        _reply("2.1", definition="4(a)(3)"),
        _reply("2.1", additions=""),  # empty heading without NONE
        _reply("2.1", unreadable="the third page"),  # no page number
        "Here are the boxes:\n" + _reply("2.1"),
        "CHECKED:\n2.1\nADDITIONS:\nNONE\nDEFINITION:\nNONE\nUNREADABLE:\nNONE",  # headings out of order
    ],
)
def test_parse_reply_is_strict(reply: str) -> None:
    with pytest.raises(reader.ReplyError):
        reader.parse_reply(reply)


# ---- reconciliation ---------------------------------------------------------------


def _read(nums: str, definition: tuple[str, ...] = (), additions: tuple[str, ...] = ()) -> reader.ReaderRead:
    return reader.ReaderRead(tuple(nums.split()), definition, additions, ())


def _pixel(checked: set[str], unreadable: tuple[int, ...] = ()) -> checkbox.PixelRead:
    cat = catalog.load()
    keys = {k: b for k, b in checkbox.geometry(cat).items() if b.page not in unreadable}
    return checkbox.PixelRead({k: (0.2 if k in checked else 0.0) for k in keys}, unreadable)


def test_reconcile_agreed_when_all_three_match() -> None:
    r = reader.reconcile(_read("2.1 6.4"), _read("2.1 6.4"), _pixel({"2.1", "6.4"}))
    assert r.agreed and r.checked == ("2.1", "6.4") and r.to_smd == ()


def test_reconcile_readers_disagree() -> None:
    r = reader.reconcile(_read("2.1 6.4"), _read("2.1"), _pixel({"2.1", "6.4"}))
    assert not r.agreed and r.checked == ()
    assert [(d.number, d.checked_by, d.not_checked_by) for d in r.differences] == [
        ("6.4", ("reader A", "pixel read"), ("reader B",))
    ]


def test_reconcile_pixel_disagrees_with_both_readers() -> None:
    r = reader.reconcile(_read("2.1"), _read("2.1"), _pixel({"2.1", "8.1"}))
    assert not r.agreed and [d.number for d in r.differences] == ["8.1"]


def test_reconcile_unregistered_page_does_not_count_against_but_goes_to_smd() -> None:
    r = reader.reconcile(_read("2.1 6.4"), _read("2.1 6.4"), _pixel({"2.1"}, unreadable=(3,)))
    assert r.agreed and r.checked == ("2.1", "6.4")
    assert any("could not register form page(s) 4" in x for x in r.to_smd)


def test_reconcile_a_failed_reader_is_a_disagreement() -> None:
    r = reader.reconcile(_read("2.1"), None, _pixel({"2.1"}))
    assert not r.agreed and "reader B gave no usable read" in r.to_smd


def test_reconcile_flags_additions_and_definition_boxes() -> None:
    a = _read("2.1", ("4(a)(1)",), ("a typed sheet adds 2.14",))
    b = _read("2.1", ("4(a)(1)",))
    r = reader.reconcile(a, b, _pixel({"2.1", "4(a)(1)"}))
    assert r.agreed and r.additions == ("a typed sheet adds 2.14",) and r.definition_boxes == ("4(a)(1)",)
    assert "handwritten or attached: a typed sheet adds 2.14" in r.to_smd
    r2 = reader.reconcile(a, _read("2.1", ("4(a)(2)",)), _pixel({"2.1", "4(a)(1)"}))
    assert not r2.agreed and {d.number for d in r2.differences} == {"4(a)(1)", "4(a)(2)"}
