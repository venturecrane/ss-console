"""Parity fix O: two new files that are one record are one exhibit (2026-10-14).

A real update pulled a 124-page file and, after it, a 404-page file whose
pages 281 to 404 were the same scans. The picture pass matched them as NEAR
copies, but it reads the new documents in PULL order, so the smaller file
kept every shared page and the larger file's copies became repeats. Both
became exhibits, every citation to the larger file's copy region pointed at
the smaller one, and where the two transcriptions of one scan disagreed (one
reading missed a value) the page was composed from both files and a visit
was itemized twice. The new documents are now read largest first
(``pageid.reading_order``), a smaller file whose every page repeats the
larger is cited there (``select.new_home``), and of two readings of one page
the one that adds nothing is not read (``update/twins``). Every test fails on
the branch head before the fix (9f151c61) and passes after. Every record is
invented."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from test_update_parity_h import _pages_run, _record, _scan, _seed, _transcribe

OVERLAP = "repeats an earlier page of the new documents"
#: Invented records whose scans a picture tells apart: each re-encoded copy
#: pairs with its own page, uniquely, at the picture module's margin.
WHOLE = (0, 1, 4, 9, 13, 15)


def _select(sr: SimpleNamespace) -> dict:
    from medchron.stages import update_select as US

    assert US.run(sr) == 0, sr.logged  # type: ignore[arg-type]
    return json.loads((sr.slug_dir / "runs" / "alpha" / "update" / "selection.json").read_text())


def _no_pain(text: str) -> str:
    """The same page read again, the reading that missed its pain score."""
    return "\n".join(x for x in text.split("\n") if not x.startswith("Pain rated"))


def _run(tmp: Path, part_texts: list[str] | None = None, whole_texts: list[str] | None = None) -> dict:
    """`part` (pulled FIRST) is pages 3 to 6 of `whole`, each a re-encoded copy
    of the same scan (a NEAR picture match, transcribed)."""
    whole = [_record(i) for i in WHOLE]
    delivered = [_record(i) for i in range(20, 22)]
    sr = _seed(
        tmp,
        {1: [_scan(t) for t in delivered]},
        {"part": [_scan(t, jpeg=40) for t in whole[2:]], "whole": [_scan(t) for t in whole]},
    )
    skip = _pages_run(sr)
    assert skip == {}  # nothing pixel-equal: every page is transcribed
    ident = json.loads((tmp / "runs" / "alpha" / "update" / "page_identity.json").read_text())
    pairs = {p: v[1:] for p, v in ident["pages"]["part"].items()}
    assert pairs == {str(q): ["whole", q + 2, "near"] for q in (1, 2, 3, 4)}  # the copy repeats the larger
    assert ident["pages"]["whole"] == {}  # the larger file is read first: its pages repeat nothing
    _transcribe(sr, {"part": part_texts or whole[2:], "whole": whole_texts or whole}, skip)
    return _select(sr)


def test_a_smaller_copy_pulled_first_is_cited_to_the_larger_file(tmp_path: Path) -> None:
    sel = _run(tmp_path)
    by_id = {r["id"]: r for r in sel["exhibits"]}
    assert set(by_id) == {"whole"}  # one record, one exhibit
    assert by_id["whole"]["exhibit"] == 2  # the next number: the copy takes none
    assert by_id["whole"]["read"] == [1, 2, 3, 4, 5, 6]  # the larger file keeps every shared page
    subs = {s["id"]: s for s in sel["subsets"]}
    assert subs["part"]["subset_of"] == {"id": "whole", "exhibit": 2, "title": by_id["whole"]["title"]}
    assert subs["part"]["matched"] == 4
    filed = [p.name for p in (tmp_path / "out" / "alpha").iterdir()]
    assert filed == [by_id["whole"]["title"] + ".pdf"], filed
    units = json.loads((tmp_path / "units" / "alpha.json").read_text())
    assert [u["id"] for u in units] == ["whole"]


def test_two_readings_of_one_page_are_never_both_composed(tmp_path: Path) -> None:
    """Where the two transcriptions of one scan disagree, the reading that
    adds nothing is not read; the one carrying a value the other missed is,
    in whichever file holds it. No page is composed from both files and no
    value is lost."""
    whole = [_record(i) for i in WHOLE]
    part = list(whole[2:])
    part[1] = _no_pain(part[1])  # the copy's reading missed a value: it adds nothing
    whole_read = list(whole)
    whole_read[4] = _no_pain(whole_read[4])  # the whole file's reading missed one: the copy keeps it
    sel = _run(tmp_path, part, whole_read)
    by_id = {r["id"]: r for r in sel["exhibits"]}
    assert by_id["whole"]["read"] == [1, 2, 3, 4, 6]
    assert by_id["whole"]["excluded"] == {OVERLAP: 1}
    assert by_id["part"]["read"] == [3]  # whole page 5, read where its pain score was read
    assert by_id["part"]["excluded"] == {OVERLAP: 3}
    twin = {q: q + 2 for q in range(1, 5)}
    assert not [q for q in by_id["part"]["read"] if twin[q] in by_id["whole"]["read"]]


def test_twins_fill_an_unpaired_page_from_its_neighbours_and_keep_a_true_disagreement() -> None:
    from medchron.update import twins as TW

    a, b, c = (_record(i) for i in (5, 6, 7))
    other_day = _record(6, "03/09/2026", 4)  # the same form, another visit
    mine = ({1: a, 2: _no_pain(b), 3: other_day}, {n: ("text", "new document") for n in (1, 2, 3)})
    theirs = ({4: a, 5: b, 6: c}, {n: ("text", "new document") for n in (4, 5, 6)})
    theirs[1].update({n: ("text", "new document") for n in (1, 2, 3)})
    theirs[0].update({n: _record(30 + n) for n in (1, 2, 3)})
    assert TW.aligned({1: 4}, 3, 6) == {1: 4, 2: 5, 3: 6}  # the unpaired pages sit at their neighbour's offset
    assert TW.settle(mine, theirs, {1: 4}) == 2
    assert mine[1][1] == ("excluded", OVERLAP)  # the same words: a repeat
    assert mine[1][2] == ("excluded", OVERLAP)  # a reading that missed a value adds nothing
    assert mine[1][3] == ("text", "new document")  # another date and value: read
    assert theirs[1][6] == ("text", "new document")
