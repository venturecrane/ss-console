"""The holes the 2026-11-12 review found in the golden harness, each closed and
proven closed by planting the defect it would have hidden:

* a child env with an empty or swapped secret (placeholders are recorded
  verbatim, never masked);
* a real secret in the shell running the tests (refused, never written);
* a broker built some other way than ``Broker.__init__`` (the golden broker is
  the real construction);
* a broker month read off the wall clock (the matrix's month is the seat clock's);
* a direct verb answering a different value, or a cell's live wire changing
  shape (the wire snapshot compares by value, and from live traffic).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from host_matrix_cells import run_cell
from host_matrix_kit import GOLDENS, diffs
from host_matrix_normal import Normal
from test_wire_snapshot import direct
from wire_kit import check_cell, read


def _got(tmp_path: Any, mp: Any, cell: str) -> list[dict[str, Any]]:
    return json.loads(json.dumps(run_cell(tmp_path, mp, cell), sort_keys=True))


def _want(cell: str) -> Any:
    return json.loads((GOLDENS / (cell.replace("/", "__") + ".json")).read_text(encoding="utf-8"))


def test_an_empty_or_swapped_secret_in_the_child_env_fails_the_golden(tmp_path, monkeypatch, real_lane_gate):
    """FALSIFIER for M2: the chronology host hands its child an empty Smokeball
    key and the Anthropic key in its place; with secrets masked to presence
    both looked right."""
    from jobs.medchron import lane as medchron_lane

    real = medchron_lane.MedchronLane._child_env

    def swapped(self: Any, job_id: str, jd: Any) -> dict[str, str]:
        env = real(self, job_id, jd)
        env["SMOKEBALL_API_KEY"] = ""
        env["SMOKEBALL_CLIENT_SECRET"] = env["ANTHROPIC_API_KEY"]
        return env

    monkeypatch.setattr(medchron_lane.MedchronLane, "_child_env", swapped)
    found = diffs(_want("medchron/delivered"), _got(tmp_path, monkeypatch, "medchron/delivered"))
    assert any(d.endswith(".env.SMOKEBALL_API_KEY") for d in found), found
    assert any(d.endswith(".env.SMOKEBALL_CLIENT_SECRET") for d in found), found


def test_a_real_secret_never_reaches_a_golden():
    """A secret value other than the matrix's placeholder is refused, not written."""
    norm = Normal([], frozenset({"ANTHROPIC_API_KEY"}))
    assert norm.env({"ANTHROPIC_API_KEY": "value-of-ANTHROPIC_API_KEY"}) == {
        "ANTHROPIC_API_KEY": "value-of-ANTHROPIC_API_KEY"
    }
    with pytest.raises(AssertionError, match="not the matrix placeholder"):
        norm.env({"ANTHROPIC_API_KEY": "sk-not-a-placeholder"})


def test_the_golden_broker_is_built_by_broker_init(tmp_path, monkeypatch):
    """FALSIFIER for M1: a change to ``Broker.__init__`` reaches every golden,
    because the golden broker is that construction, not a copy of it."""
    from bin.lib.golden_broker import build_broker
    from workspace_broker import server

    real = server.Broker.__init__
    seen: list[str] = []

    def spy(self: Any) -> None:
        import os

        seen.append(os.environ["SMD_MEDCHRON_QUEUE_DIR"])
        real(self)

    monkeypatch.setattr(server.Broker, "__init__", spy)
    b = build_broker(tmp_path, tmp_path / "audit.db", {"medchron": tmp_path / "q"})
    assert seen == [str(tmp_path / "q")]
    assert b.grants is not None and b.operations is not None, "only __init__ builds these"
    assert str(b.medchron._db.queue_dir) == str(tmp_path / "q")


def test_a_broker_month_from_the_wall_clock_fails_the_golden(tmp_path, monkeypatch, real_lane_gate):
    """FALSIFIER for the dropped month normaliser: the seat clock's month is
    pinned, so a month read anywhere else differs."""
    import datetime as dt

    from workspace_broker import demand_verbs

    real = demand_verbs.DemandVerbs._cents_this_month

    def wall(self: Any, exclude: str, now: Any = None) -> Any:
        return real(self, exclude, dt.datetime.now(dt.timezone.utc))

    monkeypatch.setattr(demand_verbs.DemandVerbs, "_cents_this_month", wall)
    found = diffs(_want("demand/delivered"), _got(tmp_path, monkeypatch, "demand/delivered"))
    assert any(d.endswith(".cents_month") or d.endswith(".month") for d in found), found


def test_a_direct_verb_answering_a_different_value_fails_the_wire(tmp_path, monkeypatch):
    """FALSIFIER for M4: a direct verb's reply compared by value, not by shape
    (the message text below keeps every type)."""
    from workspace_broker import server

    real = server.Broker.handle

    def reworded(self: Any, request: dict[str, Any], *a: Any) -> Any:
        reply = real(self, request, *a)
        if request.get("action") == "medchron_job_list" and isinstance(reply, dict):
            reply = {**reply, "planted": "x"} if reply.get("ok") else {**reply, "message": "reworded"}
        return reply

    monkeypatch.setattr(server.Broker, "handle", reworded)
    got = direct(tmp_path, monkeypatch)
    assert got["medchron_job_list"] != read()["direct"]["medchron_job_list"]


def test_a_cell_whose_live_wire_changed_fails_against_its_row(tmp_path, monkeypatch, real_lane_gate):
    """FALSIFIER for M4: the cell's own traffic is checked, not its golden file."""
    steps = _got(tmp_path, monkeypatch, "demand/delivered")
    assert check_cell("demand/delivered", steps) == []
    for step in steps:
        for w in step["wire"]:
            w["request"]["planted_key"] = 1
    assert check_cell("demand/delivered", steps) != []
