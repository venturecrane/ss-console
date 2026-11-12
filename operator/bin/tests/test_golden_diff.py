"""The golden comparison the integrity job runs (``bin/lib/golden_diff.py``):
a regenerated golden that differs is reported unless allowed-changes names the
path, a missing or extra file is reported, and a skipped fixture is ignored."""

from __future__ import annotations

import json
from pathlib import Path

from bin.lib.golden_diff import compare_dirs, diffs, unexplained


def _write(d: Path, name: str, doc: object) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_text(json.dumps(doc), encoding="utf-8")


def test_diffs_names_every_changed_path():
    a = {"x": [1, {"y": 2}], "z": "k"}
    assert diffs(a, a) == []
    assert diffs(a, {"x": [1, {"y": 3}], "z": "k"}) == ["x.1.y"]
    assert diffs(a, {"x": [1], "z": "k"}) == ["x"]
    assert diffs(a, {"x": [1, {"y": 2}]}) == ["z"]


def test_unexplained_honours_exact_and_wildcard_paths():
    assert unexplained(["a.b", "a.c.d", "e"], ["a.b", "a.c.*"]) == ["e"]


def test_a_rewritten_golden_is_reported_and_an_allowed_one_is_not(tmp_path):
    """MUTATION: drop the comparison of leaf values and the first assertion fails."""
    want, got = tmp_path / "want", tmp_path / "got"
    _write(want, "lane__delivered", {"wake": "Outcome: delivered."})
    _write(got, "lane__delivered", {"wake": "Outcome: held."})
    _write(want, "aged", {"steps": [1]})
    assert compare_dirs(want, got, skip=("aged.json",)) == {"lane__delivered": ["wake"]}
    (want / "allowed-changes.json").write_text(json.dumps({"_why": "x", "lane__delivered": ["wake"]}))
    assert compare_dirs(want, got, skip=("aged.json",)) == {}


def test_a_missing_or_extra_golden_is_reported(tmp_path):
    want, got = tmp_path / "want", tmp_path / "got"
    _write(want, "a", {})
    _write(got, "b", {})
    assert compare_dirs(want, got) == {"a": ["<file>"], "b": ["<file>"]}


def test_a_ledger_lane_reads_its_set_allowed_list_one_level_up(tmp_path):
    root = tmp_path / "ledgers"
    want, got = root / "demand", tmp_path / "out" / "demand"
    _write(want, "month", {"n": 1})
    _write(got, "month", {"n": 2})
    assert compare_dirs(want, got) == {"month": ["n"]}
    (root / "allowed-changes.json").write_text(json.dumps({"_why": "x", "demand/month": ["n"]}))
    assert compare_dirs(want, got) == {}
