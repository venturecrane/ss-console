"""A live demand job, 2026-10-07, ended HELD at the section audit (a dense
provider section ran out of its 12,000-token budget twice) after every paid
stage had succeeded. A held job cannot resume, so it would have been rerun at
full price. Three fixes, pinned here:

* the section audit asks for its model's maximum and splits a section that
  still reaches it, so an audit always finishes;
* a fault in OUR machinery ends ``failed`` (resumable, no client message); only
  something about the FILE or the request holds;
* a re-run (demand_job_rerun) is seeded from the job it supersedes and pays
  only for what is left.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import demand_testkit
from demand_testkit import DRAFT, ScriptedClient, job_doc, make_inputs, make_job, seat_with, standard_docs
from medchron_testkit import PRICING
from medchron.demand import draft, gate, seed
from medchron.demand.run import DemandRun
from test_demand_lane import FakeClient, _lane

CHECKER = Path(__file__).resolve().parents[3] / "templates" / "drafting" / "drafting_gate_check.py"


@pytest.fixture(autouse=True)
def _checker(monkeypatch):
    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))


@pytest.fixture
def pricing(tmp_path: Path) -> Path:
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(PRICING), encoding="utf-8")
    return p


def _run(tmp_path, pricing, client, job="job", seat=None, **job_kw):
    inputs = tmp_path / "inputs"
    if not inputs.is_dir():
        make_inputs(inputs)
    jd = tmp_path / job
    if not (jd / "job.json").is_file():
        make_job(jd, **job_kw)
    seat = seat or seat_with(standard_docs())
    r = DemandRun(
        jd,
        inputs_dir=str(inputs),
        pricing=str(pricing),
        seat_factory=lambda: seat,
        client=client,
        log=lambda m: None,
        readback_pause=0.0,
        vendor_factory=lambda: None,
    )
    return r, r.run(), seat


# ---- the section audit always finishes ---------------------------------------------------
class CeilingOnLongSections(ScriptedClient):
    """An audit of a section over 250 characters reaches the ceiling."""

    def _msg(self, params):
        m = super()._msg(params)
        if "AUDIT-PROMPT" in json.dumps(params.get("system")):
            body = params["messages"][-1]["content"]
            if len(body) > 250:
                m.stop_reason = "max_tokens"
        return m


def test_a_section_audit_at_the_ceiling_is_split_and_finishes(tmp_path, pricing):
    client = CeilingOnLongSections()
    _r, v, _ = _run(tmp_path, pricing, client)
    assert v.outcome == "delivered", v.reason
    audits = [c for c in client.calls if "AUDIT-PROMPT" in json.dumps(c.get("system"))]
    assert all(c["max_tokens"] == 128_000 for c in audits)  # the model's maximum, not 12,000
    assert any(len(c["messages"][-1]["content"]) <= 250 for c in audits)  # a half was audited on its own


def test_split_paragraphs_cuts_near_the_middle():
    a, b = draft.split_paragraphs("one\n\ntwo\n\nthree\n\nfour")
    assert a == "one\n\ntwo" and b == "three\n\nfour"
    assert draft.split_paragraphs("single line") == ["single line"]


# ---- ours fails, theirs holds ---------------------------------------------------------------
def _gate_refusing(monkeypatch, refusal: str):
    real = gate.run

    def fake(data, firm, md, name="gate.json"):
        out = real(data, firm, md, name=name)
        if name == "gate.json":
            out = {**out, "passed": False, "disposition": "fail_findings", "refusals": [refusal]}
        return out

    monkeypatch.setattr(gate, "run", fake)


def test_a_gate_refusal_of_our_own_output_fails_resumably(tmp_path, pricing, monkeypatch):
    _gate_refusing(monkeypatch, "[9] a visible marker is not closed")
    _r, v, seat = _run(tmp_path, pricing, ScriptedClient())
    assert v.outcome == "failed" and "drafting gate refused the letter" in v.reason and seat.sent == []


def test_a_privilege_wall_refusal_holds_for_the_attorney(tmp_path, pricing, monkeypatch):
    _gate_refusing(monkeypatch, "[1] held-out text reached the draft body")
    _r, v, seat = _run(tmp_path, pricing, ScriptedClient())
    assert v.outcome == "held" and "[1]" in v.reason and seat.sent == []


def test_an_audit_that_cannot_finish_fails_and_a_resume_pays_only_for_it(tmp_path, pricing):
    class Stuck(ScriptedClient):
        stuck = True

        def _msg(self, params):
            m = super()._msg(params)
            if (
                self.stuck
                and "AUDIT-PROMPT" in json.dumps(params.get("system"))
                and "Liability" in params["messages"][-1]["content"]
            ):
                m.stop_reason = "max_tokens"
            return m

    client = Stuck()
    _r, v, _ = _run(tmp_path, pricing, client)
    assert v.outcome == "failed" and "did not complete" in v.reason  # never held
    before = len(client.calls)
    client.stuck = False
    _r, v2, _ = _run(tmp_path, pricing, client)
    assert v2.outcome == "delivered", v2.reason
    again = client.stages()[before:]
    assert set(again) == {"AUDIT"}  # only the unfinished section's audit; every other stage was on disk


def test_file_side_reasons_still_hold(tmp_path, pricing):
    wrong = DRAFT.replace("dol: January 15, 2026", "dol: January 16, 2026")
    _r, v, _ = _run(tmp_path, pricing, ScriptedClient(draft=wrong))
    assert v.outcome == "held" and "date of loss" in v.reason  # the letter contradicts the matter record


# ---- a re-run is seeded from the job it supersedes -----------------------------------------
def test_a_rerun_starts_where_the_superseded_job_stopped(tmp_path, pricing):
    class Stuck(ScriptedClient):
        def _msg(self, params):
            m = super()._msg(params)
            if "AUDIT-PROMPT" in json.dumps(params.get("system")):
                m.stop_reason = "max_tokens"
            return m

    _r, v, _ = _run(tmp_path, pricing, Stuck(), job="old")
    assert v.outcome == "failed"
    old = tmp_path / "old"
    (old / "data" / "out").mkdir(exist_ok=True)
    (old / "data" / "out" / "stale.docx").write_text("x")
    new = make_job(tmp_path / "new", job_id="01DEMANDJOB0000000000000002")
    carried = seed.seed(new, old)
    assert {"transcribe", "summarize"} <= set(carried) and "premise" not in carried
    assert not (new / "data" / "out").exists() and not (new / "data" / "usage-ledger.jsonl").exists()
    assert not (new / "data" / "premise.json").exists()
    state = json.loads((new / "data" / "state.json").read_text())
    assert "dates" not in state and "render" not in state and "premise" not in state
    assert str(old) not in (new / "data" / "extracted.jsonl").read_text()  # paths point at the new dir
    client = ScriptedClient()
    _r, v2, _ = _run(tmp_path, pricing, client, job="new")
    assert v2.outcome == "delivered", v2.reason
    assert "DIGEST" not in client.stages() and "GAP" not in client.stages() and "COMPOSE" not in client.stages()
    assert seed.seed(new, old) == []  # never over a data dir that already has a state file


def test_a_rerun_decides_the_premise_afresh(tmp_path):
    """FALSIFIER: keep "premise" out of DROP_STATE (or premise.json out of
    DROP_PATHS) and the re-run inherits the old "settled" verdict: queued after
    a premise-gate fix, it delivered the same coverage report again (2026-10-09)."""
    old = tmp_path / "old" / "data"
    old.mkdir(parents=True)
    (old / "state.json").write_text(
        json.dumps({"pull": {"status": "done"}, "preflight": {"status": "done"}, "premise": {"status": "done"}})
    )
    (old / "premise.json").write_text(json.dumps({"passed": False}))
    (old / "coverage-report.md").write_text("COVERAGE")
    (old / "preflight.json").write_text("{}")
    new = tmp_path / "new"
    carried = seed.seed(new, tmp_path / "old")
    assert carried == ["preflight", "pull"]
    assert not (new / "data" / "premise.json").exists() and not (new / "data" / "coverage-report.md").exists()
    assert (new / "data" / "preflight.json").exists()


def test_the_lane_seeds_a_rerun_from_the_superseded_job_dir(tmp_path):
    lane, client = _lane(tmp_path)
    old_id, new_id = "01DEMAND0000000000000000AA", "01DEMAND0000000000000000AB"
    old = lane.jobs / old_id / "data"
    (old / "digest").mkdir(parents=True)
    (old / "digest.md").write_text("DIGEST")
    (old / "extracted.jsonl").write_text(json.dumps({"text_path": str(old / "text" / "a.txt")}) + "\n")
    (old / "state.json").write_text(json.dumps({"summarize": {"status": "done"}, "render": {"status": "done"}}))
    (old / "usage-ledger.jsonl").write_text("{}\n")
    client.rows[new_id] = {"state": "submitted"}
    env = {k: v for k, v in job_doc(new_id).items() if k not in ("slug", "month_cents_used")}
    (lane.queue / f"{new_id}.json").write_text(json.dumps({**env, "supersedes": old_id}))
    assert lane.tick() == "delivered"
    data = lane.jobs / new_id / "data"
    assert (data / "digest.md").read_text() == "DIGEST"
    assert json.loads((data / "state.json").read_text()) == {"summarize": {"status": "done"}}
    assert not (data / "usage-ledger.jsonl").exists()
    assert str(lane.jobs / new_id) in (data / "extracted.jsonl").read_text()


def test_a_supersedes_that_is_not_a_job_id_seeds_nothing(tmp_path):
    lane, client = _lane(tmp_path)
    new_id = "01DEMAND0000000000000000AB"
    client.rows[new_id] = {"state": "submitted"}
    env = {k: v for k, v in job_doc(new_id).items() if k not in ("slug", "month_cents_used")}
    (lane.queue / f"{new_id}.json").write_text(json.dumps({**env, "supersedes": "../../etc"}))
    assert lane.tick() == "delivered"
    assert not (lane.jobs / new_id / "data" / "state.json").exists()


_ = (demand_testkit, FakeClient)


# ---- names are not content (the leakage check) ------------------------------------------------
def test_a_document_name_shared_by_a_walled_email_is_not_leakage():
    email = "From: client@mail.example\nSubject: FW: Carrier Answer to Contact Re Med Info Resp 9.22.25\n\nmy own words to my lawyer here"
    out = gate.strip_names(email, ["Carrier Answer to Contact Re Med Info Resp 9.22.25.pdf"])
    assert "answer to contact" not in out.lower()
    assert "my own words to my lawyer here" in out  # the email's content is still checked
    assert gate.strip_names("a b c", ["Short.pdf"]) == "a b c"  # a name under four words strips nothing


def test_a_forwarded_emails_own_subject_is_stripped_without_its_prefix():
    email = "Subject: Invoice - 1234 requested from Example Firm available for Payment\n\nprivate words stay"
    out = gate.strip_names(email, ["FW_Invoice_-_1234_requested_from_Example_Firm_available_for_Payment.txt"])
    assert "available for payment" not in out.lower() and "private words stay" in out
