"""Parity fix O: two new files that are one record are one exhibit (2026-10-14).

A real update pulled a 124-page file and, after it, a 404-page file whose
pages 281 to 404 were the same scans. The picture pass matched them as NEAR
copies, but it reads the new documents in PULL order, so the smaller file
kept every shared page and the larger file's copies became repeats. Both
became exhibits, every citation to the larger file's copy region pointed at
the smaller one, and where the two transcriptions of one scan disagreed (one
reading missed a value) the page was composed from both files and a visit
was itemized twice. The new documents are now read largest first
(``pageid.reading_order``). Since 2026-11-22 a near picture sets nothing
aside: the smaller file is read, every page a suspected copy, not charged,
and the twin rule (``update/twins``) only suspects. Every record is
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
    from jobs.stages import update_select as US

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


def test_a_smaller_near_copy_is_read_its_pages_suspected_copies_of_the_larger_file(tmp_path: Path) -> None:
    from jobs.medchron import pages_record as PR

    sel = _run(tmp_path)
    by_id = {r["id"]: r for r in sel["exhibits"]}
    assert set(by_id) == {"part", "whole"} and sel["subsets"] == []
    assert by_id["whole"]["read"] == [1, 2, 3, 4, 5, 6] and by_id["part"]["read"] == [1, 2, 3, 4]
    rows = [r for r in PR.read(tmp_path / "runs" / "alpha") if r["doc"] == "part"]
    assert [(r["evidence"]["cover"]["doc"], r["evidence"]["cover"]["page"]) for r in rows] == [
        ("whole", q + 2) for q in (1, 2, 3, 4)
    ]
    assert {r["evidence"][PR.SUSPECTED] for r in rows} == {"near_picture"} and not any(map(PR.charged, rows))
    whole_rows = [r for r in PR.read(tmp_path / "runs" / "alpha") if r["doc"] == "whole"]
    assert all(map(PR.charged, whole_rows))  # the larger file, read first, is charged once


def test_a_value_one_reading_missed_is_kept_from_the_other(tmp_path: Path) -> None:
    """The larger file's reading of one scan missed its pain score; the
    copy's reading caught it. Both are read, so the value reaches compose
    (the twin rule used to rewrite one file's transcript from the other)."""
    whole = [_record(i) for i in WHOLE]
    whole_read = list(whole)
    whole_read[4] = _no_pain(whole_read[4])
    _run(tmp_path, None, whole_read)
    pain = next(x for x in whole[4].split("\n") if x.startswith("Pain rated"))
    compose = tmp_path / "runs" / "alpha" / "update" / "compose"
    assert pain not in (compose / "whole.txt").read_text() and pain in (compose / "part.txt").read_text()
    assert pain not in (tmp_path / "text" / "whole.txt").read_text()  # no transcript is rewritten


def test_twins_fill_an_unpaired_page_from_its_neighbours_and_keep_a_true_disagreement() -> None:
    from jobs.medchron.update import twins as TW

    a, b, c = (_record(i) for i in (5, 6, 7))
    other_day = _record(6, "03/09/2026", 4)  # the same form, another visit
    mine = ({1: a, 2: _no_pain(b), 3: other_day}, {n: ("text", "new document") for n in (1, 2, 3)})
    theirs = ({4: a, 5: b, 6: c}, {n: ("text", "new document") for n in (4, 5, 6)})
    theirs[1].update({n: ("text", "new document") for n in (1, 2, 3)})
    theirs[0].update({n: _record(30 + n) for n in (1, 2, 3)})
    assert TW.aligned({1: 4}, 3, 6) == {1: 4, 2: 5, 3: 6}  # the unpaired pages sit at their neighbour's offset
    assert TW.settle(mine, theirs, {1: 4}) == {}  # nothing read from another reading
    assert mine[1][1] == ("excluded", OVERLAP)  # the same words: a repeat
    assert mine[1][2] == ("excluded", OVERLAP)  # a reading that missed a value adds nothing
    assert mine[1][3] == ("text", "new document")  # another date and value: read
    assert theirs[1][6] == ("text", "new document")


def test_a_neighbour_twin_is_never_given_another_pages_reading() -> None:
    """Only the same scan (a picture pair) lends its reading. A twin found by
    its neighbours' offset alone keeps its own words: the larger page that
    adds nothing is the repeat and the smaller one is read."""
    from jobs.medchron.update import twins as TW

    a, b = _record(5), _record(6)
    mine = ({1: a, 2: b}, {n: ("text", "new document") for n in (1, 2)})
    theirs = ({4: a, 5: _no_pain(b)}, {n: ("text", "new document") for n in (4, 5)})
    theirs[0].update({n: _record(30 + n) for n in (1, 2, 3)})
    theirs[1].update({n: ("text", "new document") for n in (1, 2, 3)})
    assert TW.settle(mine, theirs, {1: 4}) == {}
    assert theirs[1][5] == ("excluded", OVERLAP) and theirs[0][5] == _no_pain(b)
    assert mine[1][2] == ("text", "new document")
    assert TW.settle(
        ({1: b}, {1: ("text", "new document")}), ({5: _no_pain(b)}, {5: ("text", "new document")}), {1: 5}
    ) == {5: 1}
