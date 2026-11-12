"""The host matrix catches a broken host: each test plants one defect in a
REAL lane host (a wrong wake text, a skipped ledger record, a child env that
grew a name, a lost wake that is never recorded) and proves the cell's golden
comparison then FAILS. An instrument that cannot fail measured nothing.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from host_matrix_cells import run_cell
from host_matrix_kit import GOLDENS, diffs


def _differs(tmp_path: Any, monkeypatch: Any, cell: str) -> list[str]:
    got = json.loads(json.dumps(run_cell(tmp_path, monkeypatch, cell), sort_keys=True))
    want = json.loads((GOLDENS / (cell.replace("/", "__") + ".json")).read_text(encoding="utf-8"))
    return diffs(want, got)


def test_the_unplanted_cell_matches(tmp_path, monkeypatch, real_lane_gate):
    """The control: with nothing planted the same cell is equal to its golden."""
    assert _differs(tmp_path, monkeypatch, "demand/delivered") == []


def test_a_wrong_wake_text_fails_the_golden(tmp_path, monkeypatch, real_lane_gate):
    from jobs.demand_lane import DemandLane

    real = DemandLane._compose_wake

    def wrong(self: Any, *a: Any, **k: Any) -> str:
        return real(self, *a, **k).replace("Outcome:", "Result:")

    monkeypatch.setattr(DemandLane, "_compose_wake", wrong)
    found = _differs(tmp_path, monkeypatch, "demand/delivered")
    assert any("wake" in d for d in found), found


def test_a_skipped_ledger_record_fails_the_golden(tmp_path, monkeypatch, real_lane_gate):
    from jobs.litigation_lane import LitigationBroker

    real = LitigationBroker.record

    def skip_delivered(self: Any, job_id: str, state: str, fields: dict[str, Any]) -> dict[str, Any]:
        return {} if state == "delivered" else real(self, job_id, state, fields)

    monkeypatch.setattr(LitigationBroker, "record", skip_delivered)
    found = _differs(tmp_path, monkeypatch, "litigation/delivered")
    assert any(".wire" in d or ".audit" in d for d in found), found


def test_a_child_env_that_grew_a_name_fails_the_golden(tmp_path, monkeypatch, real_lane_gate):
    from jobs import daemon

    monkeypatch.setattr(daemon, "CHILD_ENV_PASS", (*daemon.CHILD_ENV_PASS, "NOT_PASSED_TO_ANY_CHILD"))
    from jobs.medchron import lane as medchron_lane

    monkeypatch.setattr(medchron_lane, "CHILD_ENV_PASS", daemon.CHILD_ENV_PASS)
    found = _differs(tmp_path, monkeypatch, "medchron/delivered")
    assert any(".env." in d for d in found), found


@pytest.mark.parametrize("cell", ["drafting/wake_lost"])
def test_a_lost_wake_left_unrecorded_fails_the_golden(tmp_path, monkeypatch, real_lane_gate, cell):
    from jobs import daemon

    monkeypatch.setattr(daemon, "WAKE_MAX_ATTEMPTS", 99)
    assert _differs(tmp_path, monkeypatch, cell) != []
