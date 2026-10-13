"""The demand job's proven delivery (2026-10-13): the independent review before
anything is filed, and the read-back by file id, size and sha256 after.

Each check has a falsifier: a planted defect (a coverage report resting on a
conditional sentence, the system's own vocabulary in the letter, a wrong
party, an unsourced dollar figure, wrong bytes on the matter) and the clean
fixture beside it, which must pass with no hold. All names are invented."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from demand_testkit import DRAFT, ScriptedClient, make_inputs, make_job, make_pdf, seat_with, standard_docs
from medchron_testkit import PRICING
from medchron.demand import deliver, review as rv
from medchron.demand.run import DemandRun

CHECKER = Path(__file__).resolve().parents[3] / "templates" / "drafting" / "drafting_gate_check.py"
CLEAN = "VERDICT: 0 MUST-FIX, 0 SHOULD-FIX, 0 NOTE"
#: A carrier's notice the premise check misreads: "once you accept" is
#: conditional, but the gate's conditional guard only knows "once a/the".
CONDITIONAL = (
    "Example Mutual. Your claim number CLM-0001.\nOur insured Beta Driver.\n"
    "We close our file once you accept the policy limits."
)
PREMISE_NOT_ESTABLISHED = (
    "| MUST-FIX | 1 premise not established | G4 rests on a conditional, future statement (once you accept), "
    "not a settlement of this claim | Carrier status notice, p. 1 |\nVERDICT: 1 MUST-FIX, 0 SHOULD-FIX, 0 NOTE"
)


@pytest.fixture(autouse=True)
def _checker(monkeypatch):
    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))


@pytest.fixture
def pricing(tmp_path: Path) -> Path:
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(PRICING), encoding="utf-8")
    return p


def _run(tmp_path: Path, pricing: Path, seat, client, inputs_over=None, **job_kw):
    inputs = tmp_path / "inputs"
    if not inputs.is_dir():
        make_inputs(inputs, **(inputs_over or {}))
    jd = tmp_path / "job"
    if not (jd / "job.json").is_file():
        make_job(jd, **job_kw)
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
    return r, r.run()


def _conditional_docs():
    return standard_docs() + [("d7", "Carrier status notice.pdf", make_pdf([CONDITIONAL]), "f-corr")]


def _review_calls(client) -> list[dict]:
    return [c for c in client.calls if "INDEPENDENT PRE-DELIVERY REVIEW" in json.dumps(c.get("system"))]


def _notes_text(tmp_path: Path) -> str:
    out = tmp_path / "job" / "data" / "out" / "demand"
    return rv.docx_text(next(out.glob("* - attorney notes.docx")))


# ---- the coverage report: a premise failure its source does not establish holds ---------
def test_a_coverage_report_resting_on_a_conditional_sentence_is_held_and_nothing_is_filed(tmp_path, pricing):
    """FALSIFIER (the 2026-10-09 case): the premise gate reads a conditional
    sentence as a settlement and would file a coverage report in place of the
    demand. The review reads the quoted source and holds; zero add_file calls."""
    seat = seat_with(_conditional_docs())
    client = ScriptedClient(review=PREMISE_NOT_ESTABLISHED)
    _r, v = _run(tmp_path, pricing, seat, client)
    premise = json.loads((tmp_path / "job" / "data" / "premise.json").read_text())
    assert not premise["passed"]  # the gate itself was fooled
    assert v.outcome == "failed" and v.stage == "review" and v.reason.startswith("review_held:")
    assert seat.sent == [] and seat.created == []
    (call,) = _review_calls(client)
    user = call["messages"][0]["content"]
    # fed the premise verdict and the cited source's own text
    assert "G4 the right instrument" in user and "once you accept the policy limits" in user
    assert call["model"] == "claude-sonnet-5" and "COVERAGE POSTURE REPORT" in json.dumps(call["system"])


def test_a_redo_reviews_again_and_files(tmp_path, pricing):
    seat = seat_with(_conditional_docs())
    client = ScriptedClient(review=PREMISE_NOT_ESTABLISHED)
    _r, v = _run(tmp_path, pricing, seat, client)
    assert v.outcome == "failed" and seat.sent == []
    (tmp_path / "job" / rv.REDO_MARKER).write_text("{}")
    client.review = CLEAN
    _r, v2 = _run(tmp_path, pricing, seat, client)
    assert v2.outcome == "delivered", v2.reason
    assert len(_review_calls(client)) == 2 and v2.proof["review"] == "passed"


def test_a_release_files_the_report_with_the_released_finding_in_it(tmp_path, pricing):
    seat = seat_with(_conditional_docs())
    client = ScriptedClient(review=PREMISE_NOT_ESTABLISHED)
    _run(tmp_path, pricing, seat, client)
    (tmp_path / "job" / "release-review.json").write_text(json.dumps({"reason": "SMD read the source"}))
    _r, v = _run(tmp_path, pricing, seat, client)
    assert v.outcome == "delivered", v.reason
    assert v.proof["review"] == "released" and len(_review_calls(client)) == 1  # not paid twice
    out = tmp_path / "job" / "data" / "out" / "demand"
    report = rv.docx_text(next(out.glob("Coverage Posture Report*.docx")))
    assert "MUST-FIX" in report and "conditional" in report
    # the bytes filed are the bytes with the finding, and the read-back proves them
    manifest = json.loads((out / "upload_manifest.json").read_text())
    assert [f["sha256"] for f in v.proof["files"]] == [m["sha256"] for m in manifest]


# ---- the demand package -----------------------------------------------------------------
def test_a_clean_demand_is_delivered_with_its_proof_block(tmp_path, pricing):
    seat = seat_with(standard_docs())
    client = ScriptedClient()
    _r, v = _run(tmp_path, pricing, seat, client)
    assert v.outcome == "delivered", v.reason
    p = v.proof
    assert p["readback"] == "confirmed" and p["review"] == "passed"
    assert p["review_model"] == "claude-sonnet-5" and isinstance(p["review_cents"], int)
    manifest = json.loads((tmp_path / "job" / "data" / "out" / "demand" / "upload_manifest.json").read_text())
    assert {(f["name"], f["size"], f["sha256"]) for f in p["files"]} == {
        (m["name"], m["bytes"], m["sha256"]) for m in manifest
    }
    assert all(f["file_id"].startswith("up-") for f in p["files"])
    rec = json.loads((tmp_path / "job" / "data" / "review.json").read_text())
    assert rec["must_fix"] == 0 and rec["should_fix"] == 0  # no false hold, no false figure
    assert v.to_list()[0]["proof"] == p
    from medchron.proof import gate

    assert gate.check(v.to_list()) is None  # the daemon believes it


def test_the_review_uses_the_authored_review_model_else_audit_never_compose(tmp_path, pricing):
    seat = seat_with(standard_docs())
    client = ScriptedClient()
    over = {"models": {"compose": "claude-sonnet-5", "audit": "claude-opus-5"}}
    _r, v = _run(tmp_path / "a", pricing, seat, client, inputs_over=over)
    assert v.outcome == "delivered", v.reason
    assert {c["model"] for c in _review_calls(client)} == {"claude-opus-5"}
    seat2, client2 = seat_with(standard_docs()), ScriptedClient()
    over2 = {"models": {**over["models"], "review": "claude-haiku-4-5-20251001"}}
    _r, v2 = _run(tmp_path / "b", pricing, seat2, client2, inputs_over=over2)
    assert v2.outcome == "delivered", v2.reason
    assert {c["model"] for c in _review_calls(client2)} == {"claude-haiku-4-5-20251001"}
    assert v2.proof["review_model"] == "claude-haiku-4-5-20251001"


def test_system_vocabulary_in_the_letter_holds_before_anything_is_filed(tmp_path, pricing):
    planted = DRAFT.replace(
        "## Damages\n", "## Damages\n\nThe pipeline confirmed every figure below against the bills.\n\n", 1
    )
    seat = seat_with(standard_docs())
    _r, v = _run(tmp_path, pricing, seat, ScriptedClient(draft=planted))
    assert v.outcome == "failed" and v.reason.startswith("review_held:") and "the pipeline" in v.reason
    assert seat.sent == [] and seat.created == []


def test_an_unsourced_dollar_figure_is_should_fix_only_and_reaches_the_notes(tmp_path, pricing):
    planted = DRAFT.replace(
        "## Demand\n", "## Demand\n\nOur client also lost $9,875.00 in wages while recovering.\n\n", 1
    )
    seat = seat_with(standard_docs())
    _r, v = _run(tmp_path, pricing, seat, ScriptedClient(draft=planted))
    assert v.outcome == "delivered", v.reason  # a demand's totals are sums: never a hold
    rec = json.loads((tmp_path / "job" / "data" / "review.json").read_text())
    assert rec["must_fix"] == 0 and any("$9,875.00" in f["finding"] for f in rec["findings"])
    assert "$9,875.00 is in neither the record" in _notes_text(tmp_path)


def test_a_planted_wrong_party_is_held_by_the_model(tmp_path, pricing):
    wrong = (
        "| MUST-FIX | 4 wrong party | The Liability section names Gamma Driver as the insured; the carrier's "
        "letter names Beta Driver. | Carrier letter of acknowledgment, p. 1 |\nVERDICT: 1 MUST-FIX, 0 SHOULD-FIX, 0 NOTE"
    )
    planted = DRAFT.replace("Your insured, Beta Driver,", "Your insured, Gamma Driver,")
    seat = seat_with(standard_docs())
    client = ScriptedClient(draft=planted, review=wrong)
    _r, v = _run(tmp_path, pricing, seat, client)
    assert v.outcome == "failed" and "4 wrong party" in v.reason and seat.sent == []
    user = _review_calls(client)[0]["messages"][0]["content"]
    assert "Gamma Driver" in user  # the reviewer read the letter as it would be filed


def test_a_model_must_fix_outside_the_hold_list_is_downgraded_not_held(tmp_path, pricing):
    tone = "| MUST-FIX | 6 tone | The opening runs long. | Demand, p. 1 |\nVERDICT: 1 MUST-FIX, 0 SHOULD-FIX, 0 NOTE"
    seat = seat_with(standard_docs())
    _r, v = _run(tmp_path, pricing, seat, ScriptedClient(review=tone))
    assert v.outcome == "delivered", v.reason
    assert "SHOULD-FIX: The opening runs long." in _notes_text(tmp_path)


def test_a_released_demand_files_with_every_finding_in_the_attorney_notes(tmp_path, pricing):
    planted = DRAFT.replace(
        "## Damages\n", "## Damages\n\nThe pipeline confirmed every figure below against the bills.\n\n", 1
    )
    seat = seat_with(standard_docs())
    client = ScriptedClient(draft=planted)
    _r, v = _run(tmp_path, pricing, seat, client)
    assert v.outcome == "failed" and seat.sent == []
    (tmp_path / "job" / "release-review.json").write_text(json.dumps({"reason": "attorney will strike it"}))
    _r, v2 = _run(tmp_path, pricing, seat, client)
    assert v2.outcome == "delivered", v2.reason
    assert v2.proof["review"] == "released"
    assert 'MUST-FIX: the demand says "the pipeline"' in _notes_text(tmp_path)


def test_a_review_that_does_not_finish_fails_resumably_and_files_nothing(tmp_path, pricing):
    seat = seat_with(standard_docs())
    client = ScriptedClient(review="| NOTE | 6 tone | half a row")
    _r, v = _run(tmp_path, pricing, seat, client)
    assert v.outcome == "failed" and v.stage == "review" and v.reason.startswith("review_unfinished:")
    assert seat.sent == []
    client.review = CLEAN
    _r, v2 = _run(tmp_path, pricing, seat, client)
    assert v2.outcome == "delivered", v2.reason


# ---- the read-back -------------------------------------------------------------------
def test_wrong_bytes_on_the_matter_fail_with_readback_mismatch(tmp_path, pricing):
    seat = seat_with(standard_docs())
    seat.corrupt = {"Demand.Alpha Example.docx"}
    _r, v = _run(tmp_path, pricing, seat, ScriptedClient())
    assert v.outcome == "failed" and v.reason.startswith("readback_mismatch: Demand.Alpha Example.docx")


def test_rows_the_upload_stage_proved_are_used_without_downloading_again(tmp_path):
    manifest = [{"name": "a.docx", "sha256": "ab" * 32, "bytes": 10}]
    rec = {
        "folder_id": "folder-1",
        "files": [{"name": "a.docx", "sha256": "ab" * 32, "bytes": 10, "proven": True, "file_id": "x1"}],
    }
    proof = deliver.prove(tmp_path, object(), "m", rec, manifest, lambda m: None)
    assert proof == {
        "readback": "confirmed",
        "folder_id": "folder-1",
        "files": [{"name": "a.docx", "file_id": "x1", "size": 10, "sha256": "ab" * 32}],
    }


# ---- the code checks, alone ---------------------------------------------------------------
def test_a_figure_code_computed_is_not_flagged_and_a_stray_one_is():
    facts = {"medicals": [{"charges": [{"amount": "1200.00"}, {"amount": "300.00"}]}]}
    letter = "| Provider | Amount |\n|---|---|\n| A | $1,200.00 |\n| B | $300.00 |\n| Total | $1,500.00 |\n"
    texts = {"demand": letter + "\nAnd $4,321.00 more.", "attorney_notes": "Notes."}
    found = rv.demand_code(
        texts,
        wanted=["demand"],
        sources="",
        computed=rv.computed_figures(facts, letter),
        facts={},
        letter_md=None,
        drafted=None,
    )
    flagged = [f.finding for f in found if f.check == "3 figures"]
    assert len(flagged) == 1 and "$4,321.00" in flagged[0]
    assert all(f.grade == "SHOULD-FIX" for f in found if f.check == "3 figures")


def test_a_missing_requested_deliverable_is_must_fix():
    found = rv.demand_code(
        {"gap_audit": "x"},
        wanted=["demand", "gap_audit"],
        sources="",
        computed=set(),
        facts={},
        letter_md=None,
        drafted=None,
    )
    assert {f.finding for f in found if f.grade == "MUST-FIX"} == {
        "the request asks for the demand; no file",
        "the request asks for the attorney notes; no file",
    }
