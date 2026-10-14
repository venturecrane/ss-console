"""A held job's wake carries a firm-safe hold class; the technical reason pages SMD.

2026-10-13: a litigation job held at its gates (our seed data) woke the DELIVER
turn with counts and "stopped at gates"; the skill said "surface and ask", and
the Operator emailed the firm to "resubmit the same request, or send it to
SMD". These pin the three mechanical halves of the fix:

* the wake carries ``Hold class: <token>`` from a closed set, never the reason;
* every hold our side caused pages SMD under its own code, with the reason;
* no medical record text, and no other person's name or date of birth, rides
  a chronology update's job reason.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import test_litigation_lane as lit_t
import test_proof_daemon as pd
from medchron.proof import gate, holds

# ---- the classifier -------------------------------------------------------------


CASES = [
    ("litigation", "gate_refused: the gates refused the list: save date (1526)", "smd_finishing", "gate_held"),
    (
        "litigation",
        "parity_hold: 3 values changed from the last list with no document cited",
        "smd_finishing",
        "gate_held",
    ),
    ("litigation", "leak_found: a system word in the workbook", "smd_finishing", "gate_held"),
    ("litigation", "filing_refused: a file named 'x' is already in the folder", "smd_finishing", "upload_held"),
    ("litigation", "scope_empty: no open litigation matters are in this job's scope", "nothing_in_scope", None),
    ("litigation", "something_new: never seen", "smd_finishing", "gate_held"),
    (
        "medchron",
        "update_held: the delivered chronology does not re-render to itself (4 differing paragraph(s))",
        "smd_finishing",
        "update_held",
    ),
    (
        "medchron",
        "update_held: no delivered chronology for this client is on the matter",
        "smd_finishing",
        "update_held",
    ),
    (
        "medchron",
        "update_held: the Exhibit List names exhibit(s) [7] that its folder does not hold",
        "smd_finishing",
        "update_held",
    ),
    ("medchron", "update_held: nothing new to add: the 3 new document(s) record no visit", "already_current", None),
    (
        "medchron",
        "update_held: nothing new to read: every page of the new documents is already in the delivered exhibits",
        "already_current",
        None,
    ),
    (
        "medchron",
        "update_held: 1 document(s) name a different person than the client (document ids: ['d9'])",
        "another_person",
        "identity_held",
    ),
    (
        "medchron",
        "update_held: 2 chronology documents for this client carry the newest date; name the one to update by id",
        "which_chronology",
        None,
    ),
    (
        "medchron",
        "update_held: the delivered chronology could not be retired (403); both are on the matter",
        "smd_finishing_filed",
        "upload_held",
    ),
    (
        "medchron",
        "update_held: the delivered chronology (id f1) still reads present after its delete",
        "smd_finishing_filed",
        "upload_held",
    ),
    (
        "medchron",
        "update_held: not every new file is proven; the delivered chronology is untouched",
        "smd_finishing",
        "upload_held",
    ),
    (
        "medchron",
        "upload: the update could not be filed and proven yet; last output: x",
        "smd_finishing",
        "upload_held",
    ),
    ("medchron", "refused: assemble refused: truncated or model-refused chunks", "smd_finishing", "stage_refused"),
    (
        "medchron",
        "per_job_cap_usd: the run's spend reached the job's cost cap during vision",
        "smd_finishing",
        "limit_held",
    ),
    (
        "medchron",
        "chronology_package_page_allowance_per_month: the matter's file is 900 pages",
        "smd_finishing",
        "limit_held",
    ),
    (
        "medchron",
        "monthly_budget_usd: the month's spend reached the monthly cost budget",
        "smd_finishing",
        "limit_held",
    ),
    ("medchron", "audit coverage: a live claim is not finally SUPPORTED", "smd_finishing", "gate_held"),
    (
        "medchron",
        "identity: an emailed attachment names a different person than the client",
        "another_person",
        "identity_held",
    ),
    ("medchron", "decision: 2 unplaced files", "smd_finishing", "gate_held"),
    (
        "medchron",
        "refused: list_matter: this matter already holds a delivered chronology, so the request is an update "
        "into it, not a run; nothing was read or charged. Resubmit with update: {...}",
        "rerouted_to_update",
        None,
    ),
    ("medchron", "review_held: 1 MUST-FIX", "smd_finishing", None),  # paged by settle under review_held
    ("medchron", "", "smd_finishing", "gate_held"),
]


@pytest.mark.parametrize(("lane", "reason", "cls", "code"), CASES)
def test_each_reason_maps_to_a_firm_safe_class_and_its_page(lane, reason, cls, code):
    assert holds.classify(lane, reason) == (cls, code)


def test_every_class_and_page_code_is_in_its_closed_set():
    for lane, reason, *_ in CASES:
        cls, code = holds.classify(lane, reason)
        assert cls in holds.CLASSES
        assert code is None or code in gate.CODES


def test_the_wake_line_is_a_bare_token_never_the_reason():
    line = holds.wake_line("litigation", "parity_hold: 1 values changed for Gamma Example 900666 next_court_date")
    assert line == "Hold class: smd_finishing."
    assert re.fullmatch(r"Hold class: [a-z_]+\.", line)


def test_other_lanes_are_unchanged_and_never_page_from_here():
    assert holds.classify("demand", "memo_gate: something") == ("smd_finishing", None)


# ---- the litigation wake and its page ---------------------------------------------


class _PagingClient(lit_t.FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.alerts: list[dict] = []

    def _request(self, req: dict) -> dict:
        if req["action"] == "delivery_proof_alert":
            self.alerts.append(req)
        return super()._request(req)


GATES_HELD = """
import json
print(json.dumps({"verdict": "held", "stage": "gates", "cents": 900, "matters_total": 4, "matters_reread": 1, "flags_new": 0,
                  "files": [], "folder_id": "", "reason": "gate_refused: the gates refused the list: save date (1526) for Gamma Example"}))
"""


def _lit(tmp_path: Path, script: str) -> tuple[str, _PagingClient, str]:
    lane, _ = lit_t._lane(tmp_path, script=script)
    client = _PagingClient()
    lane.broker.client = client
    lit_t._submit(lane, client)
    state = lane.tick()
    return state, client, lane._daemon_state(lit_t.JOB)["wake"]["task"]


def test_a_litigation_gate_hold_wakes_with_its_class_and_pages_smd(tmp_path):
    state, client, task = _lit(tmp_path, GATES_HELD)
    assert state == "held"
    assert "Hold class: smd_finishing." in task.splitlines()
    assert "Gamma" not in task and "gate_refused" not in task and "save date" not in task
    assert [a["code"] for a in client.alerts] == ["gate_held"]
    assert "gate_refused" in client.alerts[0]["detail"]  # the technical reason reaches SMD


def test_a_litigation_parity_hold_pages_smd_too(tmp_path):
    state, client, task = _lit(tmp_path, lit_t.HELD)
    assert state == "held" and "Hold class: smd_finishing." in task
    assert [a["code"] for a in client.alerts] == ["gate_held"]


def test_a_delivered_litigation_wake_carries_no_hold_class(tmp_path):
    state, client, task = _lit(tmp_path, lit_t.RUNNER)
    assert state == "delivered" and "Hold class" not in task and client.alerts == []


# ---- the chronology wake and its page ---------------------------------------------


def _held(tmp_path: Path, reason: str, stage: str) -> tuple[str, list[str], str]:
    d, broker, state = pd._run(tmp_path, pd._unit(outcome="held", reason=reason, stage=stage))
    task = d._daemon_state("01A")["wake"]["task"]
    return state, pd._codes(broker), task


@pytest.mark.parametrize(
    ("reason", "stage", "cls", "codes"),
    [
        (
            "update_held: the delivered chronology does not re-render to itself (3 differing paragraph(s))",
            "update_base",
            "smd_finishing",
            ["update_held"],
        ),
        (
            "update_held: the delivered chronology could not be retired (403); both are on the matter",
            "upload",
            "smd_finishing_filed",
            ["upload_held"],
        ),
        (
            "upload: the update could not be filed and proven yet; the delivered chronology is untouched",
            "upload",
            "smd_finishing",
            ["upload_held"],
        ),
        (
            "per_job_cap_usd: the run's spend reached the job's cost cap during vision; the run stopped",
            "vision",
            "smd_finishing",
            ["limit_held"],
        ),
        (
            "update_held: nothing new to add: the 2 new document(s) record no visit",
            "update_collect",
            "already_current",
            [],
        ),
    ],
)
def test_a_chronology_hold_wakes_with_its_class_and_pages_only_our_side(tmp_path, reason, stage, cls, codes):
    state, paged, task = _held(tmp_path, reason, stage)
    assert state == "held"
    assert f"Hold class: {cls}." in task.splitlines()
    assert reason not in task and "update_held" not in task
    assert paged == codes


def test_a_refused_chronology_stage_pages_stage_refused(tmp_path):
    d, broker, state = pd._run(
        tmp_path,
        pd._unit(outcome="refused", reason="assemble refused: truncated or model-refused chunks", stage="assemble"),
    )
    assert state == "held"
    assert pd._codes(broker) == ["stage_refused"]
    assert "Hold class: smd_finishing." in d._daemon_state("01A")["wake"]["task"]


def test_a_run_refused_on_a_delivered_matter_is_rerouted_and_pages_nothing(tmp_path):
    """ss-console#3177: the agent resubmits it as the UPDATE; nothing of ours broke."""
    from medchron import dag

    stage = next(s for s in dag.STAGES if s.name == "list_matter")
    reason = stage.exit_map[3][1]
    d, broker, state = pd._run(tmp_path, pd._unit(outcome="refused", reason=reason, stage="list_matter"))
    assert state == "held" and pd._codes(broker) == []
    assert "Hold class: rerouted_to_update." in d._daemon_state("01A")["wake"]["task"].splitlines()


class _RerouteBroker(pd.ReplyBroker):
    """Answers the daemon's job list (``medchron_job_status``) from ``self.jobs``."""

    def __init__(self) -> None:
        super().__init__()
        self.jobs: list[dict] = []

    def _request(self, req):
        if req["action"] == "medchron_job_status":
            return {"ok": True, "jobs": list(self.jobs)}
        return super()._request(req)


def _rerouted(tmp_path: Path):
    from medchron import dag

    reason = "refused: " + next(s for s in dag.STAGES if s.name == "list_matter").exit_map[3][1]
    d, _ = pd.ud._daemon(tmp_path)
    broker = _RerouteBroker()
    d.broker = broker
    d.job_dir("01A").mkdir(parents=True)
    (d.job_dir("01A") / "envelope.json").write_text(json.dumps({"matter": {"number": "900277"}, "requested_by": "a@x"}))
    d._write_state("01A", state="held", reason=reason, finished_at=d.clock(), attempts=1)
    pd.watch.arm_reply_watch(d, "01A")
    return d, broker


def test_a_rerouted_hold_arms_a_watch_for_the_update(tmp_path):
    d, _broker = _rerouted(tmp_path)
    w = d._daemon_state("01A")["reroute_watch"]
    assert w["matter_number"] == "900277" and not w.get("done")


def test_a_refused_resubmit_pages_smd_once_the_reply_has_gone(tmp_path):
    """The DELIVER turn submits before it replies; a reply with no UPDATE queued
    means the resubmission was refused and the firm was told SMD is finishing it."""
    d, broker = _rerouted(tmp_path)
    broker.replies = [pd._row("medchron_job:01A:1:held")]
    d.dispatch_wakes()
    assert pd._codes(broker) == ["stage_refused"]
    assert d._daemon_state("01A")["reroute_watch"]["outcome"] == "paged"
    d.dispatch_wakes()
    assert pd._codes(broker) == ["stage_refused"]  # once


def test_a_refused_resubmit_pages_by_the_deadline_even_with_no_reply(tmp_path):
    d, broker = _rerouted(tmp_path)
    d.dispatch_wakes()
    assert "stage_refused" not in pd._codes(broker)
    d.clock = lambda: 10_000_000.0
    d.dispatch_wakes()
    assert "stage_refused" in pd._codes(broker)


def test_a_queued_update_closes_the_watch_with_no_page(tmp_path):
    d, broker = _rerouted(tmp_path)
    since = d._daemon_state("01A")["reroute_watch"]["since"]
    broker.jobs = [
        {"id": "01A", "matter_number": "900277", "created_at": since},
        {"id": "01B", "matter_number": "900277", "created_at": "2999-01-01T00:00:00.000Z"},
    ]
    broker.replies = [pd._row("medchron_job:01A:1:held")]
    d.dispatch_wakes()
    assert pd._codes(broker) == []
    assert d._daemon_state("01A")["reroute_watch"]["outcome"] == "update_queued"


def test_an_update_for_another_matter_does_not_count(tmp_path):
    d, broker = _rerouted(tmp_path)
    broker.jobs = [{"id": "01B", "matter_number": "999999", "created_at": "2999-01-01T00:00:00.000Z"}]
    broker.replies = [pd._row("medchron_job:01A:1:held")]
    d.dispatch_wakes()
    assert pd._codes(broker) == ["stage_refused"]


# ---- no document text in an update's job reason ----------------------------------


def test_update_base_names_counts_and_indexes_never_the_chronology_text(tmp_path, monkeypatch):
    from medchron import unit_proof
    from medchron.stages import update_base as ub

    medical = "Patient reports 8/10 lumbar pain radiating to the left leg after the collision"
    lines: list[str] = []

    class SR:
        job = type("J", (), {"update": object()})()
        slug_dir = tmp_path
        unit = type("U", (), {"unit": "u1"})()

        @staticmethod
        def log(m: str) -> None:
            lines.append(m)

    monkeypatch.setattr(
        ub, "_locate", lambda sr: {"chronology": {"name": "c.docx", "id": "c1"}, "exhibits": [], "others": []}
    )
    monkeypatch.setattr(ub, "_pull", lambda sr, rows, dest: [])
    monkeypatch.setattr(
        ub, "_prove", lambda sr, docx, ud: (None, "foreign", [(12, medical, medical + " (edited)"), (40, medical, "")])
    )
    assert ub.run(SR()) == 2
    joined = " | ".join(lines)
    assert "lumbar" not in joined and "Patient" not in joined
    assert "12" in joined and "40" in joined
    reason = unit_proof.paged_line(lines)
    assert reason is not None and reason.startswith("update_held: ") and "2 differing paragraph" in reason
    proof = json.loads((tmp_path / "runs" / "u1" / "update" / "base_proof.json").read_text())
    assert proof["proven"] is False and medical in proof["diffs"][0][1]  # the detail stays on the seat for SMD


def test_an_identity_hold_names_document_ids_never_the_other_person(tmp_path, monkeypatch):
    from medchron.stages import update_select as us

    lines: list[str] = []
    ud = tmp_path / "runs" / "u1" / "update"

    class SR:
        slug_dir = tmp_path
        unit = type("U", (), {"unit": "u1"})()

        @staticmethod
        def log(m: str) -> None:
            lines.append(m)

    monkeypatch.setattr(us, "plan", lambda sr: {"exhibits": [], "top_exhibit": 0})
    monkeypatch.setattr(us, "update_dir", lambda sr: ud)
    monkeypatch.setattr(us, "_candidates", lambda sr, sha: ([{"id": "d9"}], 0))
    monkeypatch.setattr(
        us,
        "_identity_holds",
        lambda sr, rows: ["d9: a date of birth 01/02/1950 that is not the client's; a patient named Zedward"],
    )
    assert us.run(SR()) == 2
    joined = " | ".join(lines)
    assert "1950" not in joined and "Zedward" not in joined
    assert "d9" in joined and "name a different person than the client" in joined
    assert holds.classify("medchron", lines[-1]) == ("another_person", "identity_held")
    assert "Zedward" in (ud / "identity_holds.json").read_text()  # SMD's detail, on the seat
