"""The integrity job's coverage and verdicts (``bin/goldens/golden-integrity.py``).

Every golden set with an allowed list is regenerated from the merge-base by
some family (a set it skipped could be rewritten unchecked), and the verdict
rules fail where they must: a dropped case, a newly not-applicable cell, a
changed normaliser, an unnamed difference, a stale entry, and an inherited
entry left in place to excuse a difference; a deleted base entry claims the
difference at its path.
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


def test_a_merged_changes_leftover_entries_block_no_peer_pr():
    """FALSIFIER: an entry the base carried that explains nothing here is a merged
    change's leftover and passes; one the PR itself added that explains nothing
    still fails as stale; an inherited one that explains a difference still fails."""
    gi = _integrity()
    behaviour, lost = {"cell": ["0.wire.1"]}, {}
    leftover = {"other": ["0.log.0"]}
    assert gi.judge("hm", {"cell": ["0.wire.1"], **leftover}, leftover, behaviour, lost) == []
    assert "stale" in gi.judge("hm", {"cell": ["0.wire.1", "0.log.0"]}, {}, behaviour, lost)[0]
    assert "excuse" in gi.judge("hm", {"cell": ["0.wire.1"]}, {"cell": ["0.wire.1"]}, behaviour, lost)[0]


#: A base entry the PR's list keeps (a leftover that explains nothing): removal
#: claims only from a list that still names something.
KEPT = {"keep": ["9.kept.0"]}


def test_removal_claims_a_difference_at_the_path_it_deleted():
    """FALSIFIER for "removal claims it": the merge-base listed 0.wire.1 and this
    change deleted it, so the difference there is named by the deletion (the
    old rule failed it as unnamed), and it is reported as claimed by removal."""
    gi = _integrity()
    behaviour = {"cell": ["0.wire.1"]}
    base = {"cell": ["0.wire.1"], **KEPT}
    assert gi.judge("hm", KEPT, base, behaviour, {}) == []
    assert gi.judge("hm", {"cell": [], **KEPT}, base, behaviour, {}) == []
    assert gi.claimed(KEPT, base, behaviour, {}) == ["cell/0.wire.1"]
    assert gi.claimed_notices("hm", KEPT, base, behaviour, {}) == ["claimed by removal: hm/cell/0.wire.1"]


def test_a_deleted_or_emptied_list_claims_nothing():
    """FALSIFIER: removal claims only entries taken out of a list that still
    names other paths. A PR that deletes the list file (read as {}) or empties
    it, then rewrites a golden at one of the base's paths, has named nothing:
    wiping the list is not a review of any one path."""
    gi = _integrity()
    base = {"cell": ["0.wire.1", "0.log.0"]}
    behaviour = {"cell": ["0.wire.1"]}
    for emptied in ({}, {"cell": []}, {"cell": [], "other": []}):
        assert gi.judge("hm", emptied, base, behaviour, {}) == ["hm/cell: behaviour: 0.wire.1"], emptied
        assert gi.claimed(emptied, base, behaviour, {}) == [], emptied
    # The same removal from a list that keeps another entry does claim it.
    kept = {"cell": ["0.log.0"]}
    assert gi.judge("hm", kept, base, behaviour, {}) == []
    assert gi.claimed(kept, base, behaviour, {}) == ["cell/0.wire.1"]


def test_a_claim_is_a_github_warning_on_the_pr(capsys):
    """Each claim prints as a workflow annotation, so it shows on the PR."""
    gi = _integrity()
    gi.announce(["claimed by removal: hm/cell/0.wire.1", "seat_callers: introduced by this PR"])
    out = capsys.readouterr().out.splitlines()
    assert "::warning::golden integrity: claimed by removal: hm/cell/0.wire.1" in out
    assert "golden integrity: NOTICE claimed by removal: hm/cell/0.wire.1" in out
    assert "golden integrity: NOTICE seat_callers: introduced by this PR" in out
    assert not any(ln.startswith("::warning::") and "introduced" in ln for ln in out)


def test_removal_claims_a_lost_value_too():
    gi = _integrity()
    base = {"cell": ["0.log.0"], **KEPT}
    assert gi.judge("hm", KEPT, base, {}, {"cell": ["0.log.0"]}) == []
    assert gi.claimed(KEPT, base, {}, {"cell": ["0.log.0"]}) == ["cell/0.log.0"]


def test_removal_claims_only_the_path_and_set_it_deleted():
    """A deleted entry names its own (set, path) and nothing else: a difference
    at another path, or at the same path in another set, is still unnamed."""
    gi = _integrity()
    base = {"cell": ["0.wire.1"], **KEPT}
    got = gi.judge("hm", KEPT, base, {"cell": ["0.wire.1", "0.wire.2"], "other": ["0.wire.1"]}, {})
    assert got == ["hm/cell: behaviour: 0.wire.2", "hm/other: behaviour: 0.wire.1"]
    assert gi.claimed(KEPT, base, {"other": ["0.wire.1"]}, {}) == []


def test_a_deleted_entry_that_explains_nothing_passes_and_claims_nothing():
    gi = _integrity()
    assert gi.judge("hm", KEPT, {"cell": ["0.wire.1"], **KEPT}, {}, {}) == []
    assert gi.claimed(KEPT, {"cell": ["0.wire.1"], **KEPT}, {}, {}) == []


def test_an_inherited_entry_left_in_place_that_explains_a_difference_fails_and_says_delete_it():
    """FALSIFIER: the base's entry, kept, does not excuse this change's
    difference; the message tells the author the deletion names the change
    (the old message did not), and nothing is claimed by removal."""
    gi = _integrity()
    base = {"cell": ["0.wire.1"]}
    got = gi.judge("hm", {"cell": ["0.wire.1"]}, base, {"cell": ["0.wire.1"]}, {})
    assert len(got) == 1
    assert "inherited" in got[0] and "excuse" in got[0]
    assert "Delete each from the list; the deletion names your change" in got[0]
    assert gi.claimed({"cell": ["0.wire.1"]}, base, {"cell": ["0.wire.1"]}, {}) == []


def test_the_unchanged_branches_of_the_rule():
    """Own entry names its difference; an unnamed difference fails; an own
    entry that explains nothing is stale; an inherited leftover blocks nothing.
    Unchanged by "removal claims it", so these pass on the old rule too."""
    gi = _integrity()
    behaviour = {"cell": ["0.wire.1"]}
    assert gi.judge("hm", {"cell": ["0.wire.1"]}, {}, behaviour, {}) == []
    assert gi.judge("hm", {}, {}, behaviour, {}) == ["hm/cell: behaviour: 0.wire.1"]
    assert "stale" in gi.judge("hm", {"cell": ["0.wire.1", "0.log.0"]}, {}, behaviour, {})[0]
    leftover = {"other": ["0.log.0"]}
    assert gi.judge("hm", {"cell": ["0.wire.1"], **leftover}, leftover, behaviour, {}) == []


def test_the_push_to_main_no_longer_requires_empty_lists():
    """Leftovers may sit on main (rule 4): the option that failed the push run
    while any list named a path is gone (a workflow still passing it would make
    the script exit 2, an argparse error, not pass)."""
    gi = _integrity()
    assert not hasattr(gi, "require_empty")
    try:
        gi.parse(["--base", "x", "--require-empty"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("--require-empty is still accepted")


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
