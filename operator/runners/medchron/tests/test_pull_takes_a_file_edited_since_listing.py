"""A file edited after the listing is fetched as it is now, not failed forever.

2026-10-23 (A&P litigation list, 230 matters): three files were edited in
Smokeball between the 10:21 listing and their fetch. Every re-pull was checked
against the stale listed size, so the run stopped ``fetch_unfinished`` and no
retry could ever pass. A size mismatch now pulls once more unchecked; two pulls
of the same new size are the file as it is, and the row records the change. A
download whose size keeps moving still fails.

FALSIFIER: drop the unchecked re-pull in ``_fetch_one`` and the first test's
row is ``ok: False`` with "size mismatch".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from medchron.demand import pull


class _Seat:
    def __init__(self, sizes: list[int]) -> None:
        self.sizes = list(sizes)  # the byte count each successive pull returns
        self.pulls: list[int | None] = []

    def mint(self, matter_id: str, file_ids: list[str]) -> list[dict[str, Any]]:
        return [{"id": f, "url": f"https://fake.invalid/{f}"} for f in file_ids]

    def fetch(self, url: str, dest: Path, expected_size: int | None) -> int:
        self.pulls.append(expected_size)
        n = self.sizes.pop(0) if len(self.sizes) > 1 else self.sizes[0]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"x" * n)
        if expected_size and n != expected_size:
            dest.unlink()
            raise RuntimeError(f"size mismatch: got {n}, expected {expected_size}; re-pull")
        return n


def _target(size: int) -> list[dict[str, Any]]:
    return [{"id": "a", "name": "a.docx", "ext": ".docx", "folder": "f", "size": size}]


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pull.time, "sleep", lambda _s: None)


def test_a_file_edited_since_listing_is_taken_at_its_new_size(tmp_path: Path) -> None:
    seat = _Seat([64369])
    rows = pull.fetch_all(seat, "m", _target(60704), tmp_path, lambda _m: None)
    assert rows[0]["ok"] is True
    assert rows[0]["size_changed"] == {"listed": 60704, "fetched": 64369}
    assert (tmp_path / "raw" / "a.docx").stat().st_size == 64369
    assert seat.pulls == [60704, None]  # one checked pull, one unchecked


def test_a_download_whose_size_keeps_moving_still_fails(tmp_path: Path) -> None:
    seat = _Seat([500, 400, 300, 200, 100, 90, 80])
    rows = pull.fetch_all(seat, "m", _target(1000), tmp_path, lambda _m: None)
    assert rows[0]["ok"] is False and "size mismatch" in rows[0]["error"]
    assert not (tmp_path / "raw" / "a.docx").exists()


def test_a_matching_size_is_one_checked_pull(tmp_path: Path) -> None:
    seat = _Seat([3])
    rows = pull.fetch_all(seat, "m", _target(3), tmp_path, lambda _m: None)
    assert rows[0]["ok"] is True and "size_changed" not in rows[0]
    assert seat.pulls == [3]
