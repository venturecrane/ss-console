"""A response job whose plan has a form interrogatory unit plans (``counts.py``).

The falsifier: #4203 put the firm's set-size line into every unit's
instruction, and ``counts.target_line`` looked up the set's plural name before
asking whether there was any number to write. A form set has no set-size line
and no plural in ``set_items.NAMES``, so a first-draft response job with an
Operator-drafted form set (FROG + SROG, the common package) raised KeyError at
the plan, and ``run()``'s own handler raised it again, ending the child with no
verdict at all. Every name and number is invented; no model is called.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from drafting_testkit import make_inputs, make_job, seat_with, standard_docs
from jobs.drafting import served
from jobs.drafting.forms import policy as form_policy
from jobs.drafting.run import DraftingRun
from test_drafting_review import Client
from test_drafting_served_form import _form_set, _srog
from test_forms_answer_stage import POLICY


@pytest.fixture
def pricing(tmp_path):
    from jobs_testkit import PRICING

    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(PRICING), encoding="utf-8")
    return p


def _inputs(root: Path) -> Path:
    """The firm's inputs with its form-response policy authored and its shell pinned."""
    make_inputs(root, **{form_policy.KEY: POLICY})
    shell = root / POLICY["skeleton"]
    shell.parent.mkdir(parents=True, exist_ok=True)
    shell.write_text("# FORM SHELL\n\n{{FORM RESPONSES}}\n", encoding="utf-8")
    y = root / "drafting-firm.yaml"
    data = yaml.safe_load(y.read_text(encoding="utf-8"))
    data["inputs"][POLICY["skeleton"]] = hashlib.sha256(shell.read_bytes()).hexdigest()
    y.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return root


def _response_run(tmp_path: Path, pricing: Path) -> DraftingRun:
    jd = make_job(tmp_path / "job", cls="discovery_response")
    doc = json.loads((jd / "job.json").read_text())
    (jd / "job.json").write_text(json.dumps({**doc, "request_text": "Please draft responses to the discovery."}))
    (jd / served.FILE).write_text(json.dumps({"sets": [_srog(), _form_set("operator")]}))
    r = DraftingRun(
        jd,
        inputs_dir=str(_inputs(tmp_path / "inputs")),
        pricing=str(pricing),
        seat_factory=lambda: seat_with(standard_docs()),
        client=Client(),
        log=lambda _m: None,
        readback_pause=0.0,
    )
    (r.data / "facts.json").write_text(json.dumps({"client_names": ["Gamma Example"], "matter_number": "100001"}))
    return r


def test_a_response_job_with_an_operator_form_set_plans_its_form_unit(tmp_path, pricing):
    r = _response_run(tmp_path, pricing)
    units = r.units()  # the plan stage's own path (finish.units -> counts.instruction)
    assert [u.variant for u in units] == ["special_interrogatories", "form_interrogatories"]
    assert "SET SIZE" not in (units[1].instruction or "")  # a form set has no set-size line
    assert r._current() != "file"  # the stage reader the run() handler uses reads the same plan


class _Planned(Exception):
    """Raised just after the plan: the test reads what the job settled."""


def test_the_run_itself_finishes_its_plan_stage_with_the_form_unit(tmp_path, pricing, monkeypatch):
    r = _response_run(tmp_path, pricing)
    plan = DraftingRun._plan_before_spend
    seen: dict = {}

    def after_plan(self):
        plan(self)
        seen["state"] = self._state()
        raise _Planned()

    monkeypatch.setattr(DraftingRun, "_plan_before_spend", after_plan)
    v = r.run()  # _walk -> _plan_before_spend -> the "plan" stage -> finish.units
    assert "state" in seen, (v.outcome, v.stage, v.reason)
    assert seen["state"]["plan"]["status"] == "done", (v.stage, v.reason)
    assert [u["variant"] for u in seen["state"]["units"]] == ["special_interrogatories", "form_interrogatories"]


def test_an_error_while_naming_the_failed_stage_still_ends_in_a_failed_verdict(tmp_path, pricing, monkeypatch):
    r = _response_run(tmp_path, pricing)

    def broken(_self):
        raise KeyError("form_interrogatories")

    monkeypatch.setattr(DraftingRun, "_walk", broken)
    monkeypatch.setattr(DraftingRun, "_current", broken)
    v = r.run()
    assert v.outcome == "failed" and v.reason.startswith("unexpected: KeyError"), (v.stage, v.reason)
