"""The chronology lifecycle matrix (``lifecycle_matrix.py``): every state x
every event, and every declared exit class, graded on where the job ends.

Each cell must pass assertions (a)-(e) (``lifecycle_matrix.problems``) and
equal its row in ``goldens/lifecycle/medchron.json``. A stage or exit code
with no cell fails the completeness test. One command rewrites the golden
(read the diff):

    python runners/jobs/tests/lifecycle_matrix.py --write    (from operator/, in the runner venv)

The other builders' new stages and exit codes become cells by that command too.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from typing import Any

import pytest
from lifecycle_matrix import FIXED_EVENTS, GOLDEN, NOT_APPLICABLE, cells, exit_classes, problems, run_cell, states

RUN = [c for c in cells() if c not in NOT_APPLICABLE]
WRITE = bool(os.environ.get("JOBS_WRITE_LIFECYCLE_GOLDENS"))


def _golden() -> dict[str, Any]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_every_stage_exit_and_state_has_a_cell():
    """MUTATION: a stage that declares an exit code with no cell fails here."""
    from jobs.medchron import dag

    want = set()
    for flavor, update in (("run", False), ("update", True)):
        for s in dag.stages(update=update):
            for code in s.exit_map:
                want.add(f"running/exit.{flavor}.{s.name}.{code}")
    have = set(cells())
    assert want <= have, sorted(want - have)
    assert {c.split("/")[0] for c in have} == set(states())
    assert set(NOT_APPLICABLE) <= have
    assert all(len(why) > 40 and "(" in why for why in NOT_APPLICABLE.values())
    if not WRITE:
        assert set(_golden()["cells"]) == set(RUN), sorted(set(RUN) ^ set(_golden()["cells"]))


def test_a_new_exit_with_no_row_fails_the_completeness_check():
    """The completeness check can fail: a copied stage list with one fake exit
    yields cells the golden does not hold."""
    from jobs.medchron import dag

    def stages(update: bool = False) -> tuple[Any, ...]:
        seq = list(dag.stages(update=update))
        seq[0] = replace(seq[0], exit_map={**seq[0].exit_map, 99: ("held", "a fake exit")})
        return tuple(seq)

    extra = set(cells(stages)) - set(cells())
    assert extra == {
        f"running/exit.run.{dag.stages()[0].name}.99",
        f"running/exit.update.{dag.stages(update=True)[0].name}.99",
    }
    assert not extra & set(_golden()["cells"])


def test_the_cells_are_states_by_events_plus_the_exit_classes():
    assert len(FIXED_EVENTS) == len(set(FIXED_EVENTS))
    assert len(cells()) == len(states()) * len(FIXED_EVENTS) + len(exit_classes())


@pytest.mark.parametrize("cell", RUN)
def test_a_cell_ends_owned_with_a_way_out_one_message_one_set_and_a_run_record(
    tmp_path, monkeypatch, real_lane_gate, cell
):
    """(a)-(e) for every cell, and the cell equals its golden row."""
    got = json.loads(json.dumps(run_cell(tmp_path, monkeypatch, cell), sort_keys=True))
    if WRITE:
        parts = GOLDEN.parent / "parts"
        parts.mkdir(parents=True, exist_ok=True)
        (parts / (cell.replace("/", "__") + ".json")).write_text(json.dumps(got, sort_keys=True), encoding="utf-8")
    assert problems(cell, got) == []
    if not WRITE:
        assert got == _golden()["cells"][cell]


def test_write_the_golden():
    """Under JOBS_WRITE_LIFECYCLE_GOLDENS, after every cell above (the command
    runs serially, in file order): the cells, combined into the one golden."""
    if not WRITE:
        return
    parts = GOLDEN.parent / "parts"
    got = {p.stem.replace("__", "/"): json.loads(p.read_text(encoding="utf-8")) for p in sorted(parts.glob("*.json"))}
    assert set(got) == set(RUN), sorted(set(RUN) ^ set(got))
    doc = {"count": len(RUN), "not_applicable": NOT_APPLICABLE, "cells": got}
    GOLDEN.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    for p in parts.glob("*.json"):
        p.unlink()
    parts.rmdir()
