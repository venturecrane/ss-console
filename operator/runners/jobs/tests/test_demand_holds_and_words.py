"""Five fixes from the 2026-11-06 and 07 demand triage, each on invented fixtures.

* A job stopped at the firm's per-job cost cap before anything was paid is
  ``failed`` (the one ending a person can resume), and SMD's approved amount
  for that job widens the cap for that job alone, so the resume goes straight
  back to its estimate: the free pull is not run again.
* The gap audit is instructed in the firm's words (the "what the firm reads"
  block, a record headed "THE RECORD"), and its text is put in the firm's
  words before it is kept, so "not in digest" is "not in the record" on disk.
* Every code-authored sentence the firm reads (``demand/firm_text.py``) passes
  the review's own deterministic vocabulary checks.
* A review hold's reason is one line, and every finding rides beside it.
* A final pass that cannot place a finding fails under a code that names the
  stage, never a sentence that starts with an article.
"""

from __future__ import annotations

import json
import string
from pathlib import Path

import pytest

from demand_testkit import ScriptedClient, make_inputs, make_job, seat_with, standard_docs
from jobs_testkit import PRICING
from jobs.demand import cost_approval, draft, finalpass, firm_text, gapaudit, pull, review as rv
from jobs.demand.run import FINAL_PASS_UNSETTLED, DemandRun

CHECKER = Path(__file__).resolve().parents[3] / "templates" / "drafting" / "drafting_gate_check.py"


@pytest.fixture(autouse=True)
def _checker(monkeypatch):
    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))


@pytest.fixture
def pricing(tmp_path: Path) -> Path:
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(PRICING), encoding="utf-8")
    return p


def _run(tmp_path: Path, pricing: Path, client, **inputs_over):
    inputs = tmp_path / "inputs"
    if not inputs.is_dir():
        make_inputs(inputs, **inputs_over)
    jd = tmp_path / "job"
    if not (jd / "job.json").is_file():
        make_job(jd)
    r = DemandRun(
        jd,
        inputs_dir=str(inputs),
        pricing=str(pricing),
        seat_factory=lambda: seat_with(standard_docs()),
        client=client,
        log=lambda _s: None,
        readback_pause=0.0,
        vendor_factory=lambda: None,
    )
    return r, r.run()


# ---- Q: a cost stop before anything is paid ---------------------------------------------------
def test_an_approved_amount_resumes_a_cap_stop_from_its_estimate_without_pulling_again(tmp_path, pricing, monkeypatch):
    pulls = []
    real = pull.run
    monkeypatch.setattr(pull, "run", lambda *a, **k: (pulls.append(1), real(*a, **k))[1])
    client = ScriptedClient()
    _r, v = _run(tmp_path, pricing, client, budget={"per_job_cap_usd": 1.0})
    # resumable, nothing paid: the one ending a person can resume (demand_ledger: held is final)
    assert v.outcome == "failed" and v.stage == "per_job_cap_usd" and v.dollars == 0 and client.calls == []
    assert pulls == [1]
    # a second attempt with no approval stops at the same estimate, and still does not pull
    _r, v = _run(tmp_path, pricing, client)
    assert v.outcome == "failed" and v.stage == "per_job_cap_usd" and pulls == [1]
    cost_approval.write_approval(tmp_path / "job", usd=50.0, by="SMD (test)", attempt=2)
    _r, v = _run(tmp_path, pricing, client)
    assert v.outcome == "delivered", v.reason
    assert pulls == [1]  # the free pull ran once, on the first attempt only


def test_the_approval_widens_this_jobs_cap_only_and_never_the_month(tmp_path):
    jd = tmp_path / "job"
    jd.mkdir()
    assert cost_approval.approved_usd(jd) == 0.0
    cost_approval.write_approval(jd, usd=12.5, by="SMD", attempt=1)
    cost_approval.write_approval(jd, usd=7.5, by="SMD", attempt=2)
    assert cost_approval.approved_usd(jd) == 20.0
    (jd / cost_approval.APPROVAL_FILE).write_text("not json", encoding="utf-8")
    assert cost_approval.approved_usd(jd) == 0.0  # unreadable is no approval
    (jd / cost_approval.APPROVAL_FILE).write_text(
        json.dumps({"approvals": [{"usd": -5}, {"usd": True}, {"usd": float("inf")}, {"usd": 3}]}), encoding="utf-8"
    )
    assert cost_approval.approved_usd(jd) == 3.0


class _Broker:
    def __init__(self, row):
        self.row, self.requests = row, []
        self.client = self

    def status(self, _job_id):
        return self.row

    def _request(self, req):
        self.requests.append(req)
        return {"ok": True}


class _Lane:
    def __init__(self, tmp_path: Path, row, attempts=1):
        self.broker = _Broker(row)
        self.queue = tmp_path / "queue"
        self.queue.mkdir(parents=True)
        self.root = tmp_path / "jobs"
        self.attempts = attempts

    def job_dir(self, job_id):
        p = self.root / job_id
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _daemon_state(self, _job_id):
        return {"attempts": self.attempts}

    def _chown_child(self, _path):
        pass


def test_approve_refuses_anything_but_a_cap_stop_and_resumes_one(tmp_path):
    held = _Lane(tmp_path / "a", {"state": "held", "reason": "per_job_cap_usd: x"})
    with pytest.raises(cost_approval.Refused, match="not stopped at per_job_cap_usd"):
        cost_approval.plan_approve(held, "J1", usd=10, by="SMD")
    budget = _Lane(tmp_path / "b", {"state": "failed", "reason": "monthly_budget_usd: x"})
    with pytest.raises(cost_approval.Refused, match="not stopped at per_job_cap_usd"):
        cost_approval.plan_approve(budget, "J1", usd=10, by="SMD")
    d = _Lane(tmp_path / "c", {"state": "failed", "reason": "per_job_cap_usd: the estimate is over"})
    for usd, by in ((0, "SMD"), (-1, "SMD"), (10, " ")):
        with pytest.raises(cost_approval.Refused):
            cost_approval.plan_approve(d, "J1", usd=usd, by=by)
    p = cost_approval.plan_approve(d, "J1", usd=60.255, by="SMD")
    cost_approval.apply_approve(d, p)
    assert cost_approval.approved_usd(d.job_dir("J1")) == 60.26
    (req,) = d.broker.requests
    assert req["action"] == "demand_job_resume" and req["job_id"] == "J1"
    assert "6,026 cents" in req["reason"] and "$" not in req["reason"]
    with pytest.raises(cost_approval.Refused, match="already has an approval"):
        cost_approval.plan_approve(d, "J1", usd=5, by="SMD")
    (d.queue / "J2.json").write_text("{}")
    with pytest.raises(cost_approval.Refused, match="already queued"):
        cost_approval.plan_approve(d, "J2", usd=5, by="SMD")


# ---- R: the gap audit in the firm's words -------------------------------------------------------
def _system_text(call) -> str:
    s = call["system"]
    return "".join(b["text"] for b in s) if isinstance(s, list) else str(s)


def test_the_gap_audit_and_the_audit_read_the_record_and_what_the_firm_reads(tmp_path, pricing):
    client = ScriptedClient()
    _run(tmp_path, pricing, client)
    gap = next(c for c in client.calls if "GAP-PROMPT" in json.dumps(c.get("system")))
    system = _system_text(gap)
    assert draft.FIRM_READS in system and "THE RECORD:\n" in system and "RECORD DIGEST" not in system
    audits = [c for c in client.calls if "## SECTION UNDER AUDIT" in json.dumps(c.get("messages"))]
    assert audits and all("## THE RECORD\n" in _system_text(c) for c in audits)
    assert not any("RECORD DIGEST" in _system_text(c) for c in audits)


def test_a_gap_audit_saying_not_in_digest_is_kept_saying_not_in_the_record(tmp_path, pricing, monkeypatch):
    raw = (
        "# Records and Billing Gap Audit\n\n## A. Referral and order trail\n\n"
        "Northfield Imaging MRI: not in digest; the digest shows the order only.\n"
    )
    monkeypatch.setattr(gapaudit.GapAuditor, "run", lambda self, system, free, digest: raw)
    _run(tmp_path, pricing, ScriptedClient())
    kept = (tmp_path / "job" / "data" / "gap-audit.md").read_text(encoding="utf-8")
    assert "not in the record; the record shows the order only" in kept
    assert "digest" not in kept.lower()


# ---- S: every sentence code writes for the firm passes the review's own checks ---------------
def _filled(template: str) -> str:
    names = {f for _, f, _, _ in string.Formatter().parse(template) if f}
    return template.format(**{n: "Example Item 1" for n in names})


def test_firm_text_holds_every_code_authored_sentence_and_each_passes_the_review():
    assert len(firm_text.ALL) >= 30 and "WALL_LEAD" in firm_text.ALL and "VENDOR_BEYOND" in firm_text.ALL
    for name, template in firm_text.ALL.items():
        text = _filled(template)
        assert rv.wording({"attorney_notes": text}) == [], (name, text)
        assert rv.wording({"gap_audit": text}) == [], (name, text)
        assert chr(0x2014) not in text and chr(0x2013) not in text, name


@pytest.mark.parametrize("said", ["Never read by a model; clear any of them.", "not looked up (over the cap)"])
def test_the_vocabulary_check_this_rests_on_still_catches_the_old_wording(said):
    """FALSIFIER: the two code-authored phrases that held demands on
    2026-11-07 are caught by the same check the firm_text test runs."""
    assert rv.wording({"attorney_notes": said}) != []


def test_the_code_sections_render_from_firm_text():
    from jobs.demand import vendor

    rows = [{"provider": f"Provider {i}", "status": vendor.OVER_CAP} for i in range(3)]
    rows.insert(0, {"provider": "Provider A", "status": "no vendor match"})
    out = vendor.section({"rows": rows})
    assert firm_text.VENDOR_HEAD in out and "3 more providers in the item table were not looked up" in out
    assert rv.wording({"gap_audit": out}) == []


# ---- T: a review hold's reason is one line; every finding rides beside it ---------------------
def test_a_review_hold_reason_is_one_line_and_the_verdict_carries_every_finding(tmp_path, pricing):
    review = "\n".join(
        f"| MUST-FIX | 1 contradicts the record | finding {n}: {'y' * 300} | FILE: Example.pdf, p. {n} |"
        for n in range(1, 6)
    )
    client = ScriptedClient(review=review + "\nVERDICT: 5 MUST-FIX, 0 SHOULD-FIX, 0 NOTE")
    _r, v = _run(tmp_path, pricing, client)
    assert v.outcome == "failed" and v.stage == "review" and v.reason.startswith("review_held:")
    assert "\n" not in v.reason and len(v.reason) < 300 and "finding 1" not in v.reason
    assert "5 must-fix" in v.reason and "1 contradicts the record x5" in v.reason
    assert [f["finding"][:9] for f in v.findings] == [f"finding {n}" for n in range(1, 6)]
    assert all(len(f["finding"]) > 300 for f in v.findings)  # whole, not cut
    (row,) = v.to_list()
    assert len(row["findings"]) == 5


# ---- U: the final pass failure names its stage --------------------------------------------------
def test_a_final_pass_that_cannot_place_a_finding_fails_under_its_code(tmp_path, pricing, monkeypatch):
    def unlocated(*_a, **_k):
        raise finalpass.Unlocated("a finding in 'Exhibits' matches nothing in that section")

    monkeypatch.setattr(finalpass, "settle", unlocated)
    _r, v = _run(tmp_path, pricing, ScriptedClient())
    assert v.outcome == "failed"
    assert v.reason.startswith(f"{FINAL_PASS_UNSETTLED}: the final audit pass could not settle a finding")
    assert v.reason.split(":", 1)[0] == "final_pass_unsettled"
