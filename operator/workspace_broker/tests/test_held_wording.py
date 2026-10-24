"""A held job's reply is its hold class's fixed wording, enforced at the send.

INCIDENT (2026-10-24 01:19Z, ashton-price). Litigation job
01M5MWGBKCK2E08FTCSFA03QTW was held; its DELIVER wake carried
``Hold class: smd_finishing.`` and the skill held the fixed sentence for that
class. The model bound the reply and sent its own words instead ("The
litigation status list was not produced ... tell me if you want the run
requested again. SMD will also need to look at why it stopped."), and the
broker transmitted them: the wording was an instruction in a skill, and
nothing checked it. Now the broker holds the wording (``held_wording``) and
refuses any other body for a held litigation job, naming the exact text so the
retry succeeds in one step.

A held CHRONOLOGY has no wording at all since 2026-10-24 (the Captain: the
firm hears about a chronology only when it is delivered): its bind and its
send are refused whatever the words, before any wording check.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_reply_binding import (
    ADMIN,
    IMID,
    LIT_LIBRARY,
    LIT_YAML,
    MATTER,
    FakeMailbox,
    _ack_in_thread,
    _broker,
    _call,
    _rows,
)
from workspace_broker.litigation_ledger import LitigationLedger
from workspace_broker.medchron_ledger import MedchronLedger
from workspace_broker.reply_binding import BindingRefused

IMPROVISED = (
    "The litigation status list was not produced. Decision for you: tell me if you want the run "
    "requested again. SMD will also need to look at why it stopped."
)
STATUS_SMD = (
    "The litigation status list is not filed yet, and nothing new has been put in the firm's Operator "
    "Library. Nothing is needed from you; SMD is finishing it, and the next message you get about this "
    "list will be the filed list."
)


def _lit(tmp_path: Path, state: str, reason: str | None = None, *, report: str | None = None) -> str:
    ledger = LitigationLedger(str(tmp_path / "audit.db"), tmp_path / "lq")
    env: dict = {
        "trigger": "request",
        "requester": ADMIN,
        "message_ref": IMID,
        "request_text": "Send me a fresh litigation status list.",
        "scope": {"all": True},
        "file_to_matter_id": LIT_LIBRARY,
        "file_to_matter_number": "OPS-OPERATOR-LIBRARY",
        "folder_name": "Litigation Status",
    }
    if report == "deadlines":
        env.update({"report": "deadlines", "workbooks": ["deadlines"], "folder_name": "Deadlines"})
    if report == "reassign":
        env.update(
            {
                "report": "reassign",
                "reassign": {
                    "from_names": ["Pat Doe", "Sam"],
                    "to_name": "Dana Roe",
                    "attorney_names": [],
                    "litigation_only": True,
                    "statuses": ["Open", "Pending"],
                },
            }
        )
    job = ledger.submit(env)
    ledger.record(job, "running", {})
    ledger.record(job, state, {"reason": reason} if reason else {})
    return job


def _chron(tmp_path: Path, state: str, reason: str | None = None) -> str:
    ledger = MedchronLedger(str(tmp_path / "audit.db"), tmp_path / "mq")
    job = ledger.submit(
        {
            "matter": {"id": MATTER, "number": "900201", "title": "Doe v. Roe"},
            "units": [{"client_name": "Jane Doe", "surname": "Doe", "dob": "01/01/1980"}],
            "incident": {"date": "2025-01-01", "source": "matter_layout"},
            "requested_by": ADMIN,
            "request_ref": IMID,
        },
        remaining=1000,
    )
    ledger.record(job, "running", {})
    ledger.record(job, state, {"reason": reason} if reason else {})
    return job


def _send_body(tmp_path: Path, box: FakeMailbox, kind: str, job: str, body: str, *, html: str = "") -> dict:
    payload = {"comment": body, **({"html": html} if html else {})}
    return _call(
        _broker(tmp_path, box, LIT_YAML),
        "msgraph_reply_bound",
        {"kind": kind, "job_id": job},
        payload=payload,
        session_id="sess-1",
    )


def _box() -> FakeMailbox:
    box = FakeMailbox()
    _ack_in_thread(box)
    return box


# -- the incident ---------------------------------------------------------------


def test_the_incidents_improvised_held_reply_is_refused(tmp_path: Path) -> None:
    """FALSIFIER: on the code before this, the improvised body went out."""
    job = _lit(tmp_path, "held", "gate_refused: 3 values without a document")
    box = _box()
    with pytest.raises(BindingRefused) as refused:
        _send_body(tmp_path, box, "litigation_job", job, IMPROVISED)
    assert box.replies() == []
    # The refusal names the exact text to send, so one retry succeeds.
    assert STATUS_SMD in str(refused.value)
    # Nothing was claimed: the retry with the fixed wording still has its one reply.
    out = _send_body(tmp_path, box, "litigation_job", job, STATUS_SMD)
    assert out["recipients"] == [ADMIN] and len(box.replies()) == 1
    assert any(r.get("outcome") == "send_refused" for r in _rows(_broker(tmp_path, box, LIT_YAML), "REPLY_BINDING"))


def test_the_refusal_reaches_the_overlay_under_its_own_code(tmp_path: Path) -> None:
    job = _lit(tmp_path, "held", "gate_refused: x")
    box = _box()
    from workspace_broker.request_errors import error_response_for

    with pytest.raises(BindingRefused) as refused:
        _send_body(tmp_path, box, "litigation_job", job, IMPROVISED)
    reply = error_response_for(refused.value)  # what the socket writes back
    assert reply["ok"] is False and reply["error"] == "HeldWordingRefused"
    assert STATUS_SMD in reply["message"]


def test_the_exact_wording_passes_with_line_breaks_and_rendered_html(tmp_path: Path) -> None:
    job = _lit(tmp_path, "held", "gate_refused: x")
    box = _box()
    wrapped = STATUS_SMD.replace("; SMD", ";\nSMD").replace(" Nothing is", "\n\nNothing is")
    html = "<p>" + STATUS_SMD.replace("firm's", "firm&#x27;s") + "</p>"
    assert _send_body(tmp_path, box, "litigation_job", job, wrapped, html=html)["recipients"] == [ADMIN]


@pytest.mark.parametrize(
    "body",
    [
        "Hi,\n\n" + STATUS_SMD,
        STATUS_SMD + "\n\nThanks",
        STATUS_SMD.replace("SMD is finishing it", "SMD is working on it"),
        STATUS_SMD.rstrip("."),
    ],
)
def test_any_other_difference_is_refused(tmp_path: Path, body: str) -> None:
    job = _lit(tmp_path, "held", "gate_refused: x")
    box = _box()
    with pytest.raises(BindingRefused):
        _send_body(tmp_path, box, "litigation_job", job, body)
    assert box.replies() == []


def test_a_correct_text_part_cannot_carry_a_different_html_part(tmp_path: Path) -> None:
    job = _lit(tmp_path, "held", "gate_refused: x")
    box = _box()
    with pytest.raises(BindingRefused):
        _send_body(tmp_path, box, "litigation_job", job, STATUS_SMD, html=f"<p>{IMPROVISED}</p>")
    assert box.replies() == []


def test_the_bind_names_the_wording_before_the_draft(tmp_path: Path) -> None:
    job = _lit(tmp_path, "held", "gate_refused: x")
    out = _call(_broker(tmp_path, _box(), LIT_YAML), "msgraph_reply_bind", {"kind": "litigation_job", "job_id": job})
    assert out["bound"] is True and out["held_wording"] == [STATUS_SMD]


# -- every class, every lane ------------------------------------------------------

LIT_CASES = [
    # (report, reason, the exact text)
    ("status", "gate_refused: x", STATUS_SMD),
    ("status", "something nobody classified", STATUS_SMD),
    (
        "status",
        "scope_empty: no open litigation matters are in this job's scope",
        "No list was filed: none of the firm's open litigation matters fit this request. Nothing is needed from you.",
    ),
    (
        "deadlines",
        "filing_refused: x",
        "The deadlines report is not filed yet, and nothing new has been put in the firm's Operator Library. "
        "Nothing is needed from you; SMD is finishing it, and the next message you get about this report will "
        "be the filed report.",
    ),
    (
        "deadlines",
        "scope_empty: no Open or Pending matters are in this job's scope",
        "No report was filed: none of the firm's Open or Pending matters fit this request. Nothing is needed from you.",
    ),
    (
        "reassign",
        "gate_refused: x",
        "The cases have not all been moved yet. Nothing is needed from you; SMD is finishing it, and the next "
        "message you get about this will say what was moved.",
    ),
    (
        "reassign",
        "staff_unresolved: to inactive",
        "Nothing was changed: Dana Roe is not an active staff member in Smokeball, so cases cannot move to "
        "them, so I did not guess. Which person did you mean? Send the request again with their full name as "
        "Smokeball shows it; that is all that is needed.",
    ),
    (
        "reassign",
        "staff_unresolved: from ambiguous",
        "Nothing was changed: Sam matches more than one person on the firm's staff list in Smokeball, so I "
        "did not guess. Which person did you mean? Send the request again with their full name as Smokeball "
        "shows it; that is all that is needed.",
    ),
]


@pytest.mark.parametrize(("report", "reason", "text"), LIT_CASES)
def test_each_litigation_class_accepts_its_wording_and_refuses_the_rest(
    tmp_path: Path, report: str, reason: str, text: str
) -> None:
    job = _lit(tmp_path, "held", reason, report=None if report == "status" else report)
    box = _box()
    with pytest.raises(BindingRefused) as refused:
        _send_body(tmp_path, box, "litigation_job", job, IMPROVISED)
    assert text in str(refused.value)
    assert _send_body(tmp_path, box, "litigation_job", job, text)["recipients"] == [ADMIN]


def test_staff_unresolved_refuses_a_name_the_request_did_not_give(tmp_path: Path) -> None:
    job = _lit(tmp_path, "held", "staff_unresolved: to unknown", report="reassign")
    box = _box()
    wrong = (
        "Nothing was changed: Lee Example is not on the firm's staff list in Smokeball, so I did not guess. "
        "Which person did you mean? Send the request again with their full name as Smokeball shows it; that "
        "is all that is needed."
    )
    with pytest.raises(BindingRefused) as refused:
        _send_body(tmp_path, box, "litigation_job", job, wrong)
    assert "Nothing was changed: Dana Roe is not on the firm's staff list in Smokeball" in str(refused.value)
    assert box.replies() == []


def test_staff_unresolved_refuses_the_wrong_kind(tmp_path: Path) -> None:
    job = _lit(tmp_path, "held", "staff_unresolved: to unknown", report="reassign")
    box = _box()
    with pytest.raises(BindingRefused):
        _send_body(
            tmp_path,
            box,
            "litigation_job",
            job,
            "Nothing was changed: Dana Roe matches more than one person on the firm's staff list in Smokeball, "
            "so I did not guess. Which person did you mean? Send the request again with their full name as "
            "Smokeball shows it; that is all that is needed.",
        )
    assert box.replies() == []


CHRON_SMD = (
    "The chronology for matter 900201 is not filed yet, and nothing new has been put on the matter. Nothing "
    "is needed from you; SMD is finishing it, and the next message you get about this chronology will be the "
    "filed one."
)
CHRON_SMD_UPDATE = (
    "The chronology for matter 900201 is not filed yet, and nothing new has been put on the matter; the "
    "chronology already there is unchanged. Nothing is needed from you; SMD is finishing it, and the next "
    "message you get about this chronology will be the filed one."
)
CHRON_OLD_WORDINGS = [
    ("review_held: 2 must-fix findings", CHRON_SMD),
    ("per_job_cap_usd: over", CHRON_SMD_UPDATE),
    (
        "update_held: nothing new to add",
        "The chronology for matter 900201 already covers every visit the new records hold, so nothing was added "
        "and the chronology is unchanged. Nothing is needed from you.",
    ),
    (
        "refused: list_matter: this matter already holds a delivered chronology; submit an update",
        "The new records for matter 900201 are going into the chronology already on the matter, so it stays one "
        "complete document. Nothing is needed from you; the next message you get about this chronology will be "
        "the updated one.",
    ),
    (
        "identity: doc 1234 names another person",
        "Before the chronology for matter 900201 can go on, one question: Records - Valley Clinic.pdf names "
        "someone other than the client. Is it the client's record? A yes or no in this thread is all that is "
        "needed.",
    ),
]


@pytest.mark.parametrize(("reason", "text"), CHRON_OLD_WORDINGS)
def test_a_held_chronology_sends_nothing_even_in_its_old_fixed_wording(tmp_path: Path, reason: str, text: str) -> None:
    """2026-10-24: the firm hears about a chronology only when it is delivered.
    The bind is refused and so is a send, whatever the words, and the bind
    names no held wording. FALSIFIER: drop the held-chronology refusal in
    ``reply_binding._verify_job`` and the old wording goes out."""
    job = _chron(tmp_path, "held", reason)
    box = _box()
    bound = _call(_broker(tmp_path, box, LIT_YAML), "msgraph_reply_bind", {"kind": "medchron_job", "job_id": job})
    assert bound["bound"] is False and "Send nothing to anyone" in bound["reason"] and "held_wording" not in bound
    for body in (text, IMPROVISED):
        with pytest.raises(BindingRefused) as refused:
            _send_body(tmp_path, box, "medchron_job", job, body)
        assert "only when it is delivered" in str(refused.value)
    assert box.replies() == []


def test_the_chronology_has_no_held_wording() -> None:
    from workspace_broker import held_wording

    assert "medchron_job" not in held_wording.KIND_LANE and "medchron" not in held_wording.WORDING


# -- what is unaffected -------------------------------------------------------------


@pytest.mark.parametrize("kind", ["litigation_job", "medchron_job"])
def test_a_delivered_reply_is_not_held_to_fixed_wording(tmp_path: Path, kind: str) -> None:
    job = _lit(tmp_path, "delivered") if kind == "litigation_job" else _chron(tmp_path, "delivered")
    box = _box()
    out = _send_body(tmp_path, box, kind, job, "Filed: 64 matters, 3 new flags.")
    assert out["recipients"] == [ADMIN] and len(box.replies()) == 1
    bound = _call(_broker(tmp_path, FakeMailbox(), LIT_YAML), "msgraph_reply_bind", {"kind": kind, "job_id": job})
    assert "held_wording" not in bound


@pytest.mark.parametrize("kind", ["litigation_job", "medchron_job"])
def test_a_failed_job_reaches_no_firm_send_at_all(tmp_path: Path, kind: str) -> None:
    """The brief asked whether a failed job can reach a firm send: it cannot.
    The bind and the send both refuse before any held-wording check runs."""
    job = _lit(tmp_path, "failed") if kind == "litigation_job" else _chron(tmp_path, "failed")
    box = _box()
    bound = _call(_broker(tmp_path, box, LIT_YAML), "msgraph_reply_bind", {"kind": kind, "job_id": job})
    assert bound["bound"] is False and "Send nothing to anyone" in bound["reason"]
    for body in (STATUS_SMD, CHRON_SMD, IMPROVISED):
        with pytest.raises(BindingRefused) as refused:
            _send_body(tmp_path, box, kind, job, body)
        assert "Send nothing to anyone" in str(refused.value)
    assert box.replies() == []


def test_the_refusal_message_is_json_safe_and_bounded() -> None:
    from workspace_broker import held_wording

    row = {"state": "held", "reason": "gate_refused: x", "report": None}
    texts = held_wording.allowed("litigation_job", row)
    assert texts == [STATUS_SMD]
    json.dumps(texts)
