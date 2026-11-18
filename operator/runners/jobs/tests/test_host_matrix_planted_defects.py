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

    # A host that kept its own copy of the list reads it from its module.
    if hasattr(medchron_lane, "CHILD_ENV_PASS"):
        monkeypatch.setattr(medchron_lane, "CHILD_ENV_PASS", daemon.CHILD_ENV_PASS)
    found = _differs(tmp_path, monkeypatch, "medchron/delivered")
    assert any(".env." in d for d in found), found


@pytest.mark.parametrize("cell", ["drafting/wake_lost"])
def test_a_lost_wake_left_unrecorded_fails_the_golden(tmp_path, monkeypatch, real_lane_gate, cell):
    from jobs import daemon

    monkeypatch.setattr(daemon, "WAKE_MAX_ATTEMPTS", 99)
    assert _differs(tmp_path, monkeypatch, cell) != []


# -- the chronology lifecycle matrix (lifecycle_matrix.py): one planted defect per assertion --------


def _lifecycle(tmp_path: Any, monkeypatch: Any, cell: str) -> list[str]:
    from lifecycle_matrix import problems, run_cell as run_life

    return problems(cell, run_life(tmp_path, monkeypatch, cell))


@pytest.mark.parametrize("cell", ["submitted/claim", "held/claim", "running/broker_down"])
def test_the_unplanted_lifecycle_cells_pass(tmp_path, monkeypatch, real_lane_gate, cell):
    """The control: with nothing planted, these cells pass (a)-(e)."""
    assert _lifecycle(tmp_path, monkeypatch, cell) == []


def test_a_stop_nobody_is_paged_about_fails_a(tmp_path, monkeypatch, real_lane_gate):
    from jobs.proof import watch

    monkeypatch.setattr(watch, "alert", lambda *a, **k: False)
    assert any(p.startswith("(a)") for p in _lifecycle(tmp_path, monkeypatch, "held/claim"))


def test_an_ending_no_verb_accepts_fails_b(tmp_path, monkeypatch, real_lane_gate):
    """The defect the matrix found: an ending the broker could not take is never recorded again."""
    from jobs.medchron import unrecorded

    monkeypatch.setattr(unrecorded, "retry", lambda d: True)
    assert any(p.startswith("(b)") for p in _lifecycle(tmp_path, monkeypatch, "running/broker_down"))


def test_a_delivery_the_firm_never_hears_of_fails_c(tmp_path, monkeypatch, real_lane_gate):
    from jobs.proof import watch

    monkeypatch.setattr(watch, "wake_owed", lambda *a, **k: False)
    assert any(p.startswith("(c)") for p in _lifecycle(tmp_path, monkeypatch, "submitted/claim"))


def test_a_second_filed_set_fails_d(tmp_path, monkeypatch, real_lane_gate):
    from jobs.proof import watch

    real = watch._record_proven

    def twice(d: Any, job_id: str, outcomes: list[Any]) -> bool:
        real(d, job_id, outcomes)
        return real(d, job_id, outcomes)

    monkeypatch.setattr(watch, "_record_proven", twice)
    assert any(p.startswith("(d)") for p in _lifecycle(tmp_path, monkeypatch, "submitted/claim"))


def test_an_ending_with_no_run_record_fails_e(tmp_path, monkeypatch, real_lane_gate):
    from jobs import run_record

    real = run_record.block
    monkeypatch.setattr(run_record, "block", lambda event, **k: None if event == "end" else real(event, **k))
    assert any(p.startswith("(e)") for p in _lifecycle(tmp_path, monkeypatch, "submitted/claim"))
