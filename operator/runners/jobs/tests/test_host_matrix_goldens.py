"""The job host matrix (``host_matrix_kit.py``): every lane host x every
ending its code reaches, compared with ``goldens/host_matrix/``.

A change to how any lane host claims, runs, records, wakes or waits must
leave every cell equal to its golden, except the dotted paths
``goldens/host_matrix/allowed-changes.json`` names for that cell (a reviewed
change, made in the PR that changes the behaviour). A cell the code cannot
reach is declared in ``host_matrix_kit.NOT_APPLICABLE`` with its reason; any
other missing cell fails.

``JOBS_WRITE_HOST_GOLDENS=1`` rewrites the goldens (read the diff).
``JOBS_GOLDENS_OUT=<dir>`` writes them to ``<dir>`` instead of comparing: the
integrity job (``operator/bin/goldens/golden-integrity.py``) runs this file
against the merge-base tree that way.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from host_matrix_cells import CELLS, run_cell
from host_matrix_kit import ENDINGS, GOLDENS, LANES, NOT_APPLICABLE, diffs, load_allowed, unexplained


def _file(cell: str) -> str:
    return cell.replace("/", "__") + ".json"


def test_every_cell_is_captured_or_declared_not_applicable():
    """MUTATION: drop a cell from the matrix without a reason and this fails."""
    every = {f"{lane}/{ending}" for lane in LANES for ending in ENDINGS}
    assert set(CELLS) | set(NOT_APPLICABLE) == every
    assert not set(CELLS) & set(NOT_APPLICABLE)
    assert all(len(why) > 20 for why in NOT_APPLICABLE.values())
    on_disk = {p.name for p in GOLDENS.glob("*__*.json")}
    assert on_disk == {_file(c) for c in CELLS}


@pytest.mark.parametrize("cell", CELLS)
def test_a_cell_matches_its_golden(tmp_path, monkeypatch, real_lane_gate, cell):
    """MUTATION: change what a lane host records, wakes, writes or launches for any ending and its cell fails."""
    got = json.loads(json.dumps(run_cell(tmp_path, monkeypatch, cell), sort_keys=True))
    out = os.environ.get("JOBS_GOLDENS_OUT")
    if out or os.environ.get("JOBS_WRITE_HOST_GOLDENS"):
        dest = Path(out) / "host_matrix" if out else GOLDENS
        dest.mkdir(parents=True, exist_ok=True)
        (dest / _file(cell)).write_text(json.dumps(got, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        if out:
            return
    want: Any = json.loads((GOLDENS / _file(cell)).read_text(encoding="utf-8"))
    allowed = load_allowed(GOLDENS).get(_file(cell)[: -len(".json")], [])
    assert unexplained(diffs(want, got), allowed) == []
