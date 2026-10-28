"""SMD's decision on a chronology held at the cycle page allowance (2026-10-28).

The hold pages SMD with the figures and the two commands; ``approve`` resumes
the job with an approved overage through the broker's resume; ``decline`` ends
it and tells SMD the requester has not been told (the chronology skill has no
authored wording for a stopped chronology, so no wake is written). Every other
hold pages exactly as it did.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import test_proof_daemon as pd
import test_upload_daemon as ud
from medchron import allowance

JOB = "01A"
HOLD = (
    "chronology_package_page_allowance_per_month: the matter's file is 4,210 pages and 3,000 pages "
    "remain in the cycle ending Nov 14's allowance; the package was not started"
)
FIG = {"pages": 4210, "remaining": 3000, "over": 1210, "projected_cents": 8734}


class _Broker(ud.FakeBroker):
    """Also answers the root resume verb, as the real broker would."""

    def __init__(self) -> None:
        super().__init__()
        self.resumes: list[dict] = []
        self.refuse_resume = False

    def _request(self, req):
        if req["action"] == "medchron_job_resume":
            if self.refuse_resume:
                raise ValueError("refused for the test")
            self.resumes.append(req)
            return {"ok": True, "queued": True}
        return super()._request(req)


def _held(tmp_path: Path, *outcomes: dict):
    """A job the daemon ran to an allowance hold, through the real settle."""
    outs = outcomes or (pd._unit(outcome="held", reason=HOLD, stage="vision", allowance=FIG),)
    d, broker = ud._daemon(tmp_path, pd._runner(*outs))
    real = _Broker()
    real.rows, real.records, real.proof_requests = broker.rows, broker.records, broker.proof_requests
    d.broker = real
    real.rows[JOB] = {"state": "submitted"}
    (d.queue / f"{JOB}.json").write_text(json.dumps(ud._envelope(JOB)))
    state = d.tick()
    real.rows[JOB].update(reason=real.records[-1][2].get("reason"))
    # The real runner also writes its verdict as a file (verdict.py); the
    # fake one only prints it.
    (d.job_dir(JOB) / "verdict.json").write_text(json.dumps(list(outs)))
    return d, real, state


def _alerts(broker) -> list[dict]:
    return [r for r in broker.proof_requests if r["action"] == "delivery_proof_alert"]


# ---- the page ----------------------------------------------------------------
def test_the_allowance_hold_pages_smd_with_the_figures_and_both_commands(tmp_path):
    d, broker, state = _held(tmp_path)
    assert state == "held"
    (a,) = _alerts(broker)
    assert a["code"] == "limit_held"
    detail = a["detail"]
    assert detail == (
        "allowance hold: matter 2026-PI-102; the job reads 4,210 pages, 3,000 remain, over by 1,210; "
        "projected_cents 8,734. Approve: python -m medchron.allowance approve 01A --by NAME | "
        "Decline: python -m medchron.allowance decline 01A --by NAME"
    )
    # Nothing a client reads: no name, no DOB, no dollar text, and within the
    # broker's 300-character detail even for a real 26-character job id.
    assert "Example" not in detail and "1980" not in detail and "$" not in detail and "USD" not in detail
    assert len(allowance.alert_detail("0" * 26, "2026-PI-102", {**FIG, "pages": 99_999})) <= 300
    # No wake: a held chronology sends the firm nothing.
    st = d._daemon_state(JOB)
    assert "wake" not in st
    assert st["allowance_hold"] == {**FIG, "attempt": 1}
    # The ledger's reason stays the dollar-free hold sentence.
    assert broker.records[-1][2]["reason"] == HOLD


def test_a_non_allowance_hold_pages_exactly_as_it_did(tmp_path):
    reason = "per_job_cap_usd: the run's spend reached the job's cost cap during vision; the run stopped"
    d, broker, _state = _held(tmp_path, pd._unit(outcome="held", reason=reason, stage="vision"))
    (a,) = _alerts(broker)
    assert a["code"] == "limit_held" and a["detail"] == reason
    assert "allowance_hold" not in d._daemon_state(JOB)
    with pytest.raises(allowance.Refused, match="not held on the page allowance"):
        allowance.plan_approve(d, JOB, by="Scott", pages=10)
    with pytest.raises(allowance.Refused, match="not held on the page allowance"):
        allowance.plan_decline(d, JOB, by="Scott")


# ---- approve -----------------------------------------------------------------
def test_approve_resumes_through_the_broker_with_the_overrun_by_default(tmp_path):
    d, broker, _ = _held(tmp_path)
    p = allowance.plan_approve(d, JOB, by="Scott", pages=None)
    allowance.apply_approve(d, p)
    (req,) = broker.resumes
    assert req["allowance_approved_pages"] == 1210 and req["allowance_approved_by"] == "Scott"
    assert req["redo"] == [] and req["reason"].startswith("allowance overage approved by SMD: 1,210 pages")
    assert allowance.approved_pages(d.job_dir(JOB)) == 1210
    assert d._daemon_state(JOB)["allowance_decision"]["decision"] == "approved"


def test_approve_takes_an_explicit_page_count(tmp_path):
    d, broker, _ = _held(tmp_path)
    allowance.apply_approve(d, allowance.plan_approve(d, JOB, by="Scott", pages=2000))
    assert broker.resumes[0]["allowance_approved_pages"] == 2000
    assert allowance.approved_pages(d.job_dir(JOB)) == 2000


def test_approve_refuses_twice_on_the_same_hold(tmp_path):
    d, _broker, _ = _held(tmp_path)
    allowance.apply_approve(d, allowance.plan_approve(d, JOB, by="Scott", pages=None))
    # The marker the real broker writes is in the queue now, and the decision
    # is recorded: either alone refuses a second approval.
    (d.queue / f".resume-{JOB}.json").write_text("{}")
    with pytest.raises(allowance.Refused, match="already queued"):
        allowance.plan_approve(d, JOB, by="Scott", pages=None)
    (d.queue / f".resume-{JOB}.json").unlink()
    with pytest.raises(allowance.Refused, match="already has a decision"):
        allowance.plan_approve(d, JOB, by="Scott", pages=None)


def test_approve_refuses_a_running_job(tmp_path):
    d, _broker, _ = _held(tmp_path)
    d._write_state(JOB, state="running")
    with pytest.raises(allowance.Refused, match="does not hold"):
        allowance.plan_approve(d, JOB, by="Scott", pages=None)


def test_approve_refuses_without_a_name_or_a_positive_count(tmp_path):
    d, _broker, _ = _held(tmp_path)
    with pytest.raises(allowance.Refused, match="--by"):
        allowance.plan_approve(d, JOB, by="  ", pages=None)
    with pytest.raises(allowance.Refused, match="--pages"):
        allowance.plan_approve(d, JOB, by="Scott", pages=0)


def test_a_refused_resume_takes_the_approval_back_out(tmp_path):
    d, broker, _ = _held(tmp_path)
    broker.refuse_resume = True
    with pytest.raises(allowance.Refused, match="taken back out"):
        allowance.apply_approve(d, allowance.plan_approve(d, JOB, by="Scott", pages=None))
    assert allowance.approved_pages(d.job_dir(JOB)) == 0
    assert "allowance_decision" not in d._daemon_state(JOB)


# ---- decline -----------------------------------------------------------------
def test_decline_ends_the_job_tells_smd_and_writes_no_wake(tmp_path):
    d, broker, _ = _held(tmp_path)
    allowance.apply_decline(d, allowance.plan_decline(d, JOB, by="Scott"))
    job, state, fields = broker.records[-1]
    assert (job, state) == (JOB, "failed")
    assert fields["reason"].startswith("chronology_package_page_allowance_per_month: declined by SMD")
    assert "has not been told" in fields["reason"]
    # The held unit read nothing it is charged for.
    assert fields["pages"] == 0
    st = d._daemon_state(JOB)
    assert st["state"] == "failed" and "wake" not in st
    last = _alerts(broker)[-1]
    assert last["code"] == "limit_held" and "DECLINED by Scott" in last["detail"]
    assert "NOT been told" in last["detail"]
    # Nothing can approve it afterwards.
    broker.rows[JOB].update(reason=fields["reason"])
    with pytest.raises(allowance.Refused):
        allowance.plan_approve(d, JOB, by="Scott", pages=None)


def test_decline_keeps_the_pages_of_a_unit_that_finished(tmp_path):
    done = pd._unit(unit="alpha", outcome="delivered")
    held = pd._unit(unit="beta", outcome="held", reason=HOLD, stage="map", allowance=FIG)
    d, broker, state = _held(tmp_path, done, held)
    assert state == "held"
    allowance.apply_decline(d, allowance.plan_decline(d, JOB, by="Scott"))
    assert broker.records[-1][2]["pages"] == done["pages"]


def test_the_decline_reason_classifies_as_a_limit_hold_for_smd():
    from medchron.proof import holds

    assert holds.classify("medchron", allowance.DECLINED_REASON) == (holds.SMD_FINISHING, "limit_held")


# ---- the job's metering: a shared file counts once ----------------------------
def test_a_joint_job_records_its_shared_file_once(tmp_path):
    """Two clients' chronologies built from one 2,048-page file read 2,048
    pages. Each unit reports the job's whole read and the job's whole spend
    (driver.Budget reads every unit's ledger), so the job's figures are the
    largest any unit saw, never the sum. FALSIFIER: the summing ``_report``
    recorded 4,096 pages and the first unit's spend twice."""
    alpha = pd._unit(unit="alpha", outcome="delivered", pages=2048, dollars=40.0, proof=pd.PROOF)
    beta = pd._unit(unit="beta", outcome="delivered", pages=2048, dollars=75.5, proof=pd.PROOF)
    d, broker = ud._daemon(tmp_path, pd._runner(alpha, beta))
    broker.rows[JOB] = {"state": "submitted"}
    (d.queue / f"{JOB}.json").write_text(json.dumps(ud._envelope(JOB)))
    assert d.tick() == "delivered"
    fields = broker.records[-1][2]
    assert (fields["pages"], fields["cents"]) == (2048, 7550)


def test_a_joint_delivery_wake_names_every_clients_folder_and_the_shared_file_once(tmp_path):
    """A joint matter, 2026-10-28: both clients' chronologies delivered, each
    to its own folder, and the requester's reply named one client because the
    wake carried one folder. The wake now names every delivered folder (ids
    only: the deliver turn reads each folder's name) and the job's pages once.
    FALSIFIER: on the old code the wake read "Delivered folder id: fold-a."
    and nothing about the second client's folder."""
    alpha = pd._unit(unit="alpha", outcome="delivered", pages=2048, folder_id="fold-a", proof=pd.PROOF)
    beta = pd._unit(unit="beta", outcome="delivered", pages=2048, folder_id="fold-b", proof=pd.PROOF)
    d, broker = ud._daemon(tmp_path, pd._runner(alpha, beta))
    broker.rows[JOB] = {"state": "submitted"}
    (d.queue / f"{JOB}.json").write_text(json.dumps(ud._envelope(JOB)))
    assert d.tick() == "delivered"
    fields = broker.records[-1][2]
    assert fields["delivery"]["folders"] == ["fold-a", "fold-b"]
    task = d._daemon_state(JOB)["wake"]["task"]
    assert "Delivered folder ids (one chronology per client, 2 clients): fold-a, fold-b." in task
    assert "Delivered folder id: " not in task
    assert "pages: 2048;" in task and "4096" not in task


def test_the_deliver_skill_reads_the_joint_folder_line_and_names_every_client():
    """The seat reads SKILL.md at turn time: DELIVER must know the joint line
    the daemon writes and must say to name every client's chronology."""
    skill = Path(__file__).resolve().parents[3] / "skills" / "medical-chronology-maintainer" / "SKILL.md"
    deliver = " ".join(skill.read_text(encoding="utf-8").split("## DELIVER", 1)[1].split("## ANSWER", 1)[0].split())
    assert "`Delivered folder ids (one chronology per client, <n> clients):`" in deliver
    assert "never name one client of a joint delivery alone" in deliver
    assert "never add, split or repeat a count per client" in deliver


def test_a_single_client_delivery_wake_is_unchanged(tmp_path):
    d, broker = ud._daemon(tmp_path, pd._runner(pd._unit(folder_id="fold-a", proof=pd.PROOF)))
    broker.rows[JOB] = {"state": "submitted"}
    (d.queue / f"{JOB}.json").write_text(json.dumps(ud._envelope(JOB)))
    assert d.tick() == "delivered"
    assert "folders" not in broker.records[-1][2]["delivery"]
    assert "Delivered folder id: fold-a." in d._daemon_state(JOB)["wake"]["task"]
