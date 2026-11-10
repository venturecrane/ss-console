"""The byte-identity assertion behind the remaining vendored-copy gate can fail.

An assertion over a list of copies has a failure mode a single comparison does
not: an empty list passes every per-copy check vacuously. This proves the
assertion turns red on drift and on a list shorter than its floor.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vendored_sync import assert_byte_identical


def test_assert_byte_identical_fails_on_drift_and_on_an_empty_list(tmp_path: Path) -> None:
    canonical = tmp_path / "canonical.py"
    canonical.write_text("x = 1\n", encoding="utf-8")
    same = tmp_path / "a.py"
    other = tmp_path / "b.py"
    same.write_text("x = 1\n", encoding="utf-8")
    other.write_text("x = 2\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="drifted"):
        assert_byte_identical(canonical, [same, other], floor=2, restamp_hint="")
    with pytest.raises(AssertionError, match="at least 1 vendored copies"):
        assert_byte_identical(canonical, [], floor=1, restamp_hint="")
    other.write_text("x = 1\n", encoding="utf-8")
    assert_byte_identical(canonical, [same, other], floor=2, restamp_hint="")
