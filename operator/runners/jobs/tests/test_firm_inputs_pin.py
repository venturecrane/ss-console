"""A job keeps the firm-inputs version it started on when the applier swaps the live one.

The seat's live inputs path is a symlink the root firm-inputs applier re-points
when a firm's configuration is republished (overlay firm_inputs_applier).
Demand and drafting read pinned files lazily, stage by stage, so every lane's
``resolve_dir`` resolves the link ONCE and each lane hands its child the
resolved path. FALSIFIER: drop ``pin`` from a lane's ``resolve_dir`` (or hand
the child ``self.inputs_dir`` raw) and the read after the swap sees the new
version.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from jobs.casefile import firm as casefile_firm
from jobs.demand import firm as demand_firm
from jobs.drafting import firm as drafting_firm
from jobs.firm_inputs import pin, pin_env
from jobs.litigation import firm as litigation_firm
from jobs.medchron import config as medchron_config
from jobs.records import firm as records_firm

LANE_MODULES = {
    "demand": demand_firm,
    "drafting": drafting_firm,
    "litigation": litigation_firm,
    "casefile": casefile_firm,
    "records": records_firm,
}


def _versions(tmp_path: Path, lane: str) -> tuple[Path, Path, Path]:
    a, b = tmp_path / f"{lane}.vaaaaaaaa", tmp_path / f"{lane}.vbbbbbbbb"
    for d, word in ((a, "old"), (b, "new")):
        d.mkdir()
        (d / "voice.md").write_text(word)
    live = tmp_path / lane
    os.symlink(a.name, live)
    return live, a, b


def _swap(live: Path, target: Path) -> None:
    tmp = live.with_name(f".{live.name}.link")
    os.symlink(target.name, tmp)
    os.replace(tmp, live)


@pytest.mark.parametrize("lane", sorted(LANE_MODULES))
def test_each_lane_resolves_the_live_link_once(tmp_path: Path, lane: str) -> None:
    live, a, b = _versions(tmp_path, lane)
    root = LANE_MODULES[lane].resolve_dir(str(live))
    assert root == a.resolve()
    _swap(live, b)
    # The job that resolved before the swap still reads its own version.
    assert (root / "voice.md").read_text() == "old"
    assert LANE_MODULES[lane].resolve_dir(str(live)) == b.resolve()


def test_the_env_default_is_pinned_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    live, a, _ = _versions(tmp_path, "demand")
    monkeypatch.setenv(demand_firm.ENV_DIR, str(live))
    assert demand_firm.resolve_dir() == a.resolve()


def test_the_chronology_file_is_pinned(tmp_path: Path) -> None:
    a = tmp_path / "medchron-firm.vaaaaaaaa.yaml"
    a.write_text("old")
    live = tmp_path / "medchron-firm.yaml"
    os.symlink(a.name, live)
    assert medchron_config.resolve_path(str(live)) == a.resolve()


def test_pin_passes_none_and_plain_paths(tmp_path: Path) -> None:
    assert pin_env(None) is None and pin_env("") is None
    assert pin(tmp_path) == tmp_path.resolve()


def test_a_records_job_child_gets_the_version_live_at_its_start(tmp_path: Path, monkeypatch) -> None:
    """Two jobs either side of a swap: each child is handed its own version, never the link."""
    import test_records_lane as trl

    import yaml

    review, decisions = tmp_path / "review", tmp_path / "decisions"
    monkeypatch.setenv(trl.rr.REVIEW_DIR_ENV, str(review))
    monkeypatch.setenv(trl.rr.DECISIONS_DIR_ENV, str(decisions))
    cy = tmp_path / "customer.yaml"
    cy.write_text(yaml.safe_dump({"matter_file_delivery": {"recipients": ["@example-firm.test"], "review_first": 0}}))
    monkeypatch.setenv("SMD_CUSTOMER_YAML", str(cy))
    monkeypatch.delenv("FAKE_VERDICT", raising=False)
    inputs_a = tmp_path / "records.vaaaaaaaa"
    inputs_b = tmp_path / "records.vbbbbbbbb"
    for d in (inputs_a, inputs_b):
        d.mkdir()
        (d / "records-firm.yaml").write_text("per_job_cap_usd: 5.0\nmonthly_budget_usd: 100.0\nusd_per_page: 0.01\n")
    live = tmp_path / "records"
    os.symlink(inputs_a.name, live)
    seat = {"review": review, "decisions": decisions, "customer": cy, "inputs": live}
    lane, client, _ = trl._lane(tmp_path, seat)
    trl._submit(lane, client)
    assert lane.tick() == "review"
    env = json.loads((lane.job_dir(trl.JOB) / "env.json").read_text())
    assert env["JOBS_RECORDS_INPUTS_DIR"] == str(inputs_a.resolve())
    _swap(live, inputs_b)
    trl._submit(lane, client, job_id=trl.JOB2, matter_id="00000000-0000-0000-0000-00000000beef")
    lane.tick()
    env2 = json.loads((lane.job_dir(trl.JOB2) / "env.json").read_text())
    assert env2["JOBS_RECORDS_INPUTS_DIR"] == str(inputs_b.resolve())
    # The first job's record is untouched by the swap.
    assert json.loads((lane.job_dir(trl.JOB) / "env.json").read_text()) == env
