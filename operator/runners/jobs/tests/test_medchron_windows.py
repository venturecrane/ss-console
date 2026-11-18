"""The writer's reading windows (``medchron/windows``): at most WINDOW_PAGES
pages of one file, cut where a note starts, every page in exactly one window.

Every name, date and value below is invented."""

from __future__ import annotations

import re
from pathlib import Path

from jobs.medchron import windows as W
from jobs.medchron.update import select as S
from jobs.stages import chunking

CHUNK = chunking.chunk_size(128000)
FILLER = "\n".join(f"Exam line {i}: findings recorded as stated by the provider." for i in range(1, 26))
OWN_MARK = re.compile(r"^\[p\.(\d+)\]", re.M)


def _note_page(dos: str, head: bool, dated: bool = True) -> str:
    """A page of a visit: the visit's first page opens with a note heading;
    a later page carries the date of service in its banner (or nothing)."""
    if head:
        return f"Progress Note\nAuthor: Example Provider, MD\nDate of Service: {dos}\n{FILLER}"
    banner = f"Example Clinic | Date of Service: {dos}\n" if dated else ""
    return f"{banner}{FILLER}"


def _visits(lengths: list[int], first_day: int = 1) -> list[str]:
    """Page texts for consecutive visits of the given lengths, one date each."""
    pages = []
    for k, n in enumerate(lengths):
        dos = f"{1 + (first_day + k) // 28:02d}/{1 + (first_day + k) % 28:02d}/2025"
        for i in range(n):
            pages.append(_note_page(dos, head=i == 0, dated=i % 3 != 2))
    return pages


def _text(pages: list[str]) -> str:
    return "".join(f"[p.{n}]\n{t}\n" for n, t in enumerate(pages, 1))


def _own_pages(part: str) -> list[int]:
    """The page marks a window carries as its own (context blocks aside)."""
    return [int(m) for m in OWN_MARK.findall(chunking.CONTEXT_BLOCK.sub("", part))]


def _visit_of(lengths: list[int]) -> dict[int, int]:
    out, p = {}, 1
    for v, n in enumerate(lengths):
        for _ in range(n):
            out[p] = v
            p += 1
    return out


def test_no_window_holds_more_than_twelve_pages_and_a_long_note_is_cut_at_twelve() -> None:
    lengths = [3, 30, 2]
    parts = W.cut(_text(_visits(lengths)), CHUNK)
    sizes = [len(_own_pages(p)) for p in parts]
    assert max(sizes) <= W.WINDOW_PAGES == 12
    assert sizes == [3, 12, 12, 8], sizes  # the 30-page note runs on; the 2-page visit packs after its tail


def test_a_five_page_note_is_never_cut_in_two() -> None:
    lengths = [4, 5, 4, 5, 3, 5, 2]
    visit = _visit_of(lengths)
    parts = W.cut(_text(_visits(lengths)), CHUNK)
    owner: dict[int, int] = {}
    for k, part in enumerate(parts):
        for p in _own_pages(part):
            owner[p] = k
    for v in range(len(lengths)):
        assert len({owner[p] for p, x in visit.items() if x == v}) == 1, f"visit {v} cut across windows"


def test_every_page_is_in_exactly_one_window_and_nothing_is_lost(tmp_path: Path) -> None:
    lengths = [2, 1, 7, 2, 13, 3, 1, 6, 2, 2]
    txt = _text(_visits(lengths))
    parts = W.cut(txt, CHUNK)
    pages = [p for part in parts for p in _own_pages(part)]
    assert pages == list(range(1, sum(lengths) + 1)), "a page lost or doubled"
    # the windows are the file's text, cut: context blocks aside, they join back to it byte for byte
    assert "".join(chunking.CONTEXT_BLOCK.sub("", p) for p in parts) == chunking.CONTEXT_BLOCK.sub("", txt)


def test_a_window_never_crosses_a_file(tmp_path: Path) -> None:
    files = []
    for i, lengths in enumerate([[2, 1], [1, 2], [3]]):
        tp = tmp_path / f"f{i}.txt"
        tp.write_text(_text(_visits(lengths)))
        files.append({"id": f"f{i}", "name": f"Example File {i}", "ext": ".pdf", "text_path": str(tp)})
    chunks = chunking.build_chunks(files, CHUNK, cut=W.cut)
    assert len(chunks) == 3, "three small files are three requests, never one"
    assert all(c.count("=== FILE:") == 1 for c in chunks)
    # without the cut (every other caller), the files still pack into one request as before
    assert len(chunking.build_chunks(files, CHUNK)) == 1


def test_a_continued_banner_never_opens_a_window() -> None:
    pages = _visits([10])
    # page 11 states a NEW date, but its banner says the note continues
    pages.append("Example Hospital | Admission (continued) | Date of Service: 03/04/2025\n" + FILLER)
    pages += _visits([4], first_day=40)
    parts = W.cut(_text(pages), CHUNK)
    firsts = [_own_pages(p)[0] for p in parts]
    assert 11 not in firsts, firsts
    starts = W.note_starts(W.pages_of(_text(pages)))
    assert starts[10] is False and starts[11] is True


def test_a_repeated_note_header_on_each_page_does_not_open_a_note() -> None:
    """An EMR prints its note header, date of service and all, on every page:
    only a new date (or a heading with no date of the note) opens a note."""
    pages = [_note_page("05/06/2025", head=True) for _ in range(4)] + [_note_page("05/07/2025", head=True)]
    starts = W.note_starts(W.pages_of(_text(pages)))
    assert starts == [True, False, False, False, True]


def test_a_page_with_no_signal_joins_the_note_before_it() -> None:
    pages = _visits([2]) + [FILLER, FILLER]
    assert W.note_starts(W.pages_of(_text(pages))) == [True, False, False, False]


def test_update_blocks_travel_with_their_page() -> None:
    """``select.selected_text`` writes a note heading block and the foot of
    the page before AHEAD of a read page's mark; a cut never separates them."""
    raw = _visits([3, 9, 3, 9, 4])
    pages = dict(enumerate(raw, 1))
    read = [p for p in pages if p not in (4, 5, 16, 17)]  # pages the delivery read are not composed
    txt = S.selected_text(pages, read)
    parts = W.cut(txt, CHUNK)
    for part in parts:
        for m in re.finditer(r"\[(?:context, the foot of the page before|note heading), p\.(\d+)", part):
            following = OWN_MARK.search(part, m.end())
            assert following is not None, "a block ahead of a page was cut away from it"
    assert [p for part in parts for p in _own_pages(part)] == read


def test_a_cut_inside_a_note_carries_the_neighbouring_page_as_context() -> None:
    parts = W.cut(_text(_visits([20])), CHUNK)
    assert parts[1].startswith("[context, the foot of the page before, p.12:")
    assert "[context, the head of the page after, p.13:" in parts[0]


def test_a_text_without_page_marks_is_cut_as_before() -> None:
    txt = "A word-processing letter with no page marks.\n" * 10
    assert W.cut(txt, CHUNK) == [txt]
    assert W.cut(txt, 100) == chunking.char_parts(txt, 100)


# The measured update: 509 pages in five files (96, 78, 131, 140, 64 pages),
# visits a median of 2 pages, 6 to 7 at the 90th percentile, at most ~28.
VISIT_CYCLE = [1, 2, 2, 3, 1, 2, 6, 2, 4, 1, 2, 7, 2, 3, 1, 2, 2, 5]
FILE_PAGES = [96, 78, 131, 140, 64]
#: Requests the writer makes for that shape under the window rule (was 5).
REQUESTS_509 = 49


def _shaped_file(npages: int, start: int, long_visit: bool) -> list[str]:
    lengths, k = ([28] if long_visit else []), start
    while sum(lengths) < npages:
        lengths.append(VISIT_CYCLE[k % len(VISIT_CYCLE)])
        k += 1
    lengths[-1] -= sum(lengths) - npages
    return _visits([n for n in lengths if n > 0], first_day=start * 3)


def test_the_request_count_for_the_measured_509_page_shape(tmp_path: Path) -> None:
    files = []
    for i, n in enumerate(FILE_PAGES):
        tp = tmp_path / f"r{i}.txt"
        tp.write_text(_text(_shaped_file(n, i * 7, long_visit=i == 3)))
        files.append({"id": f"r{i}", "name": f"Example Records {i}", "ext": ".pdf", "text_path": str(tp)})
    chunks = chunking.build_chunks(files, CHUNK, cut=W.cut)
    pages = [len(chunking.pieces_pages(c)[0]["pages"]) for c in chunks]
    assert sum(pages) == 509 and max(pages) <= W.WINDOW_PAGES
    assert len(chunks) == REQUESTS_509, len(chunks)
