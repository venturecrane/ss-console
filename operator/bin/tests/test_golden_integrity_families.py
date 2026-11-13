"""The integrity job's coverage and verdicts (``bin/goldens/golden-integrity.py``).

Every golden set with an allowed list is regenerated from the merge-base by
some family (a set it skipped could be rewritten unchecked), and the verdict
rules fail where they must: a dropped case, a newly not-applicable cell, a
changed normaliser, an unnamed difference, a stale or inherited entry, and a
non-empty list on the push to main.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

from bin.goldens.integrity_families import FAMILIES, INSTRUMENT_ALLOWED
from bin.lib.golden_diff import parse_allowed

OPERATOR = Path(__file__).resolve().parents[2]


def _integrity() -> Any:
    spec = importlib.util.spec_from_file_location(
        "golden_integrity", OPERATOR / "bin" / "goldens" / "golden-integrity.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _allowed_files() -> set[str]:
    found = {p for p in OPERATOR.rglob("allowed-changes.json") if "node_modules" not in p.parts}
    found |= set(OPERATOR.rglob("*.allowed-changes.json")) | set((OPERATOR / "bin" / "goldens").glob("allowed-*.json"))
    return {p.relative_to(OPERATOR).as_posix() for p in found}


def test_every_allowed_list_in_the_tree_belongs_to_a_family():
    """MUTATION: add a golden set with its own allowed-changes.json and no
    family, and this fails: the integrity job would never regenerate it."""
    named = {f.allowed for f in FAMILIES} | {INSTRUMENT_ALLOWED}
    assert _allowed_files() == named


def test_every_family_has_goldens_and_a_valid_allowed_list():
    from bin.lib.golden_diff import goldens

    for f in FAMILIES:
        assert goldens(OPERATOR / f.root, f.pattern), f"{f.name}: no golden under {f.root}/{f.pattern}"
        parse_allowed(json.loads((OPERATOR / f.allowed).read_text(encoding="utf-8")), f.allowed)
    parse_allowed(json.loads((OPERATOR / INSTRUMENT_ALLOWED).read_text(encoding="utf-8")), INSTRUMENT_ALLOWED)


def _manifest(cells: list[str], na: list[str], norm: str = "x") -> dict[str, Any]:
    return {
        "cases": {"host_matrix.cells": cells, "host_matrix.not_applicable": na},
        "normalisers": {"host_matrix_normal._ULID": norm},
    }


def test_a_dropped_cell_a_new_not_applicable_or_a_changed_normaliser_is_named():
    """FALSIFIER for H4: the harness pinning less is reported; pinning more is not."""
    gi = _integrity()
    base = _manifest(["a/x", "a/y"], ["a/z"])
    assert gi.manifest_losses(base, _manifest(["a/x", "a/y", "a/z"], [])) == {}
    assert gi.manifest_losses(base, _manifest(["a/x"], ["a/z", "a/y"])) == {
        "<cases>": ["host_matrix.cells:a/y", "host_matrix.not_applicable:a/y"]
    }
    assert gi.manifest_losses(base, _manifest(["a/x", "a/y"], ["a/z"], norm="wider")) == {
        "<normaliser>": ["host_matrix_normal._ULID"]
    }
    assert gi.manifest_losses({}, _manifest(["a/x"], [])) == {}
    new_module = {**_manifest(["a/x", "a/y"], ["a/z"]), "normalisers": {"host_matrix_normal._ULID": "x", "kit2.f": "y"}}
    assert gi.manifest_losses(base, new_module) == {}, "a module the base lacked normalises nothing it pinned"
    added = {**_manifest(["a/x", "a/y"], ["a/z"]), "normalisers": {"host_matrix_normal._ULID": "x", "host_matrix_normal._NEW": "y"}}  # fmt: skip
    assert gi.manifest_losses(base, added) == {"<normaliser>": ["host_matrix_normal._NEW"]}


def test_the_verdict_names_unnamed_stale_and_inherited_entries():
    gi = _integrity()
    behaviour, lost = {"cell": ["0.wire.1"]}, {}
    assert gi.judge("hm", {}, {}, behaviour, lost) == ["hm/cell: behaviour: 0.wire.1"]
    assert gi.judge("hm", {"cell": ["0.wire.1"]}, {}, behaviour, lost) == []
    assert "stale" in gi.judge("hm", {"cell": ["0.wire.1", "0.log.0"]}, {}, behaviour, lost)[0]
    assert "inherited" in gi.judge("hm", {"cell": ["0.wire.1"]}, {"cell": ["0.wire.1"]}, behaviour, lost)[0]


def test_the_push_to_main_fails_while_an_allowed_list_names_a_path(tmp_path, monkeypatch):
    """FALSIFIER for "cleared once merged": a merged entry left in place fails the push run."""
    gi = _integrity()
    for f in FAMILIES:
        p = tmp_path / f.allowed
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"_why": "x"}))
    (tmp_path / INSTRUMENT_ALLOWED).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / INSTRUMENT_ALLOWED).write_text(json.dumps({"<cases>": []}))
    monkeypatch.setattr(gi, "OPERATOR", tmp_path)
    assert gi.require_empty() == []
    (tmp_path / FAMILIES[0].allowed).write_text(json.dumps({"medchron__delivered": ["0.wire.1"]}))
    assert len(gi.require_empty()) == 1


def test_a_set_is_introduced_only_when_the_base_has_none_of_its_goldens(tmp_path):
    """A set the base already has is never treated as introduced (never skipped)."""
    gi = _integrity()
    paid = next(f for f in FAMILIES if f.name == "paid_calls")
    assert gi.introduced(paid, tmp_path)
    (tmp_path / paid.root).mkdir(parents=True)
    (tmp_path / paid.root / "allowed-changes.json").write_text("{}")
    assert gi.introduced(paid, tmp_path), "an allowed list alone is not a golden"
    (tmp_path / paid.root / "demand.jsonl").write_text("{}\n")
    assert not gi.introduced(paid, tmp_path)
    assert all(f.checks for f in FAMILIES), "an introduced set with no tests of its own would pass unchecked"
