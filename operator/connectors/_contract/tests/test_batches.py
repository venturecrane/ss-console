"""Files split into replies as the overlay splits them: 3 files or 25 MiB a reply, at most 3 replies."""

from __future__ import annotations

import pytest

from records_contract import batches as b

MIB = 1024 * 1024


def _files(*sizes: int) -> list[dict]:
    return [{"file_id": f"f{i}", "name": f"f{i}.pdf", "size": s, "handle": "h"} for i, s in enumerate(sizes)]


def _shape(out: list[list[dict]]) -> list[int]:
    return [len(x) for x in out]


def test_four_files_go_three_then_one() -> None:
    assert _shape(b.batches(_files(1, 1, 1, 1))) == [3, 1]


def test_two_twenty_mib_files_go_one_each() -> None:
    assert _shape(b.batches(_files(20 * MIB, 20 * MIB))) == [1, 1]


def test_exactly_25_mib_fits_one_reply() -> None:
    assert _shape(b.batches(_files(20 * MIB, 5 * MIB))) == [2]


def test_ten_files_are_refused_with_the_staging_words() -> None:
    with pytest.raises(b.BatchesRefused, match=r"^more than 9 files were staged for one request$"):
        b.batches(_files(*([1] * 10)))
    # split itself never refuses: it is the shape the overlay computes before it refuses.
    assert _shape(b.split(_files(*([1] * 10)))) == [3, 3, 3, 1]


def test_four_replies_are_refused_with_the_reply_words() -> None:
    with pytest.raises(b.BatchesRefused, match=r"^the files need more than 3 replies$"):
        b.batches(_files(20 * MIB, 20 * MIB, 20 * MIB, 20 * MIB))


def test_nine_small_files_fill_three_replies() -> None:
    assert _shape(b.batches(_files(*([1] * 9)))) == [3, 3, 3]


def test_rows_ride_through_unchanged_and_empty_is_empty() -> None:
    files = _files(1, 2)
    assert b.batches(files) == [files]
    assert b.batches([]) == []


def test_continuation_text() -> None:
    assert (
        b.continuation_text(2, 3, _files(1, 1)) == "Part 2 of 3 of the files you asked for, attached: f0.pdf, f1.pdf."
    )
