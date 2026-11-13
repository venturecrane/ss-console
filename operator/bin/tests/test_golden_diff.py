"""The golden comparison the integrity job runs (``bin/lib/golden_diff.py``):
a regenerated golden that differs is reported unless allowed-changes names the
path, a missing or extra file is reported, and a skipped fixture is ignored."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bin.lib.golden_diff import (
    AllowedListError,
    compare_dirs,
    diffs,
    goldens,
    inherited,
    losses,
    parse_allowed,
    read_golden,
    stale,
    tree_diffs,
    unallowed,
    unexplained,
)


def _write(d: Path, name: str, doc: object) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_text(json.dumps(doc), encoding="utf-8")


def test_diffs_names_every_changed_path():
    a = {"x": [1, {"y": 2}], "z": "k"}
    assert diffs(a, a) == []
    assert diffs(a, {"x": [1, {"y": 3}], "z": "k"}) == ["x.1.y"]
    assert diffs(a, {"x": [1], "z": "k"}) == ["x"]
    assert diffs(a, {"x": [1, {"y": 2}]}) == ["z"]


def test_unexplained_honours_exact_paths_only():
    assert unexplained(["a.b", "a.c.d", "e"], ["a.b", "a.c"]) == ["a.c.d", "e"]


# ---- the integrity job's rules (H3, H4) -------------------------------------------


def test_a_wildcard_entry_is_refused():
    """FALSIFIER: an entry covering a subtree (``0.wire.*``) would let a later
    change inside it through unreviewed; the list is refused outright."""
    with pytest.raises(AllowedListError, match="wildcard"):
        parse_allowed({"_why": "x", "medchron__delivered": ["0.wire.*"]})
    with pytest.raises(AllowedListError):
        parse_allowed({"medchron__delivered": "0.wire"})
    assert parse_allowed({"_why": "x", "a": ["0.wire.1"]}) == {"a": ["0.wire.1"]}


def test_losses_report_what_the_new_side_drops_and_never_an_addition():
    """FALSIFIER for H4: a dropped step, key or list item, or a changed value,
    is a loss; a key or a trailing item only the new side has is not."""
    old = {"steps": [{"a": 1}, {"b": 2}], "k": "v"}
    assert losses(old, old) == []
    assert losses(old, {"steps": [{"a": 1}], "k": "v"}) == ["steps.1"]
    assert losses(old, {"steps": [{"a": 1}, {"b": 2}]}) == ["k"]
    assert losses(old, {"steps": [{"a": 9}, {"b": 2}], "k": "v"}) == ["steps.0.a"]
    assert losses(old, {"steps": [{"a": 1}, {"b": 2}, {"c": 3}], "k": "v", "new": 1}) == []


def test_a_file_the_base_held_and_the_pr_drops_is_a_loss(tmp_path):
    base, pr = tmp_path / "base", tmp_path / "pr"
    _write(base, "medchron__busy", {"x": 1})
    _write(base, "medchron__held", {"x": 1})
    _write(pr, "medchron__held", {"x": 1})
    _write(pr, "medchron__new", {"x": 1})
    found = tree_diffs(goldens(base), goldens(pr), only_losses=True)
    assert found == {"medchron__busy": ["<file>"]}
    assert unallowed(found, {"medchron__busy": ["<file>"]}) == {}


def test_jsonl_and_nested_goldens_are_read(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "kit.json").write_text("{}")
    (tmp_path / "medchron.jsonl").write_text('{"n": 1}\n{"n": 2}\n')
    (tmp_path / "allowed-changes.json").write_text("{}")
    assert sorted(goldens(tmp_path, "**/*")) == ["a/kit", "medchron"]
    assert read_golden(tmp_path / "medchron.jsonl") == [{"n": 1}, {"n": 2}]


def test_an_entry_that_explains_no_difference_is_stale():
    """FALSIFIER for H3: an allowed path no difference names is reported."""
    allowed = {"a": ["0.x", "0.y"], "b": []}
    assert stale(allowed, {"a": ["0.x"]}, {}) == {"a": ["0.y"]}
    assert stale(allowed, {"a": ["0.x"]}, {"a": ["0.y"]}) == {}


def test_an_entry_the_base_already_carried_is_inherited():
    """FALSIFIER for H3: an entry merged with an earlier change cannot excuse a later one."""
    assert inherited({"a": ["0.x", "0.y"]}, {"a": ["0.x"]}) == {"a": ["0.x"]}
    assert inherited({"a": ["0.y"]}, {"b": ["0.y"]}) == {}


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
