"""The drafting job's proven delivery (2026-10-13): the independent review on
its own model (never the drafter's), SMD's redo and release, and the read-back
by file id, size and sha256 before the verdict may say delivered.

The 2026-10-12 false section 2030.050 declaration is the standing falsifier: it
must still hold, now on a reviewer that is not the drafter. All names invented."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from drafting_testkit import ScriptedClient, make_inputs, make_job, seat_with, standard_docs
from medchron_testkit import PRICING
from medchron.drafting import review
from medchron.drafting.run import DraftingRun
from medchron.demand import review as demand_review
from medchron.proof import gate

CHECKER = Path(__file__).resolve().parents[3] / "templates" / "drafting" / "drafting_gate_check.py"
CLEAN = "VERDICT: 0 MUST-FIX, 0 SHOULD-FIX, 0 NOTE"
FALSE_DECLARATION = (
    "| MUST-FIX | 1 statements | The declaration swears the set exceeds 35; it has 35 and no prior set was "
    "served. | Document 1, para 3 |\nVERDICT: 1 MUST-FIX, 0 SHOULD-FIX, 0 NOTE"
)
MODELS = {k: "claude-sonnet-5" for k in ("transcription", "digest", "compose", "repair")} | {"audit": "claude-opus-5"}


@pytest.fixture(autouse=True)
def _checker(monkeypatch):
    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))


@pytest.fixture
def pricing(tmp_path: Path) -> Path:
    p = tmp_path / "pricing.json"
    table = copy.deepcopy(PRICING)
    table["models"]["claude-opus-5-5"] = {"input_per_million_cents": 500, "output_per_million_cents": 2500}
    p.write_text(json.dumps(table), encoding="utf-8")
    return p


def _run(tmp_path: Path, pricing: Path, client, seat=None, models=None):
    inputs = tmp_path / "inputs"
    if not inputs.is_dir():
        make_inputs(inputs, models=models or MODELS)
    jd = tmp_path / "job"
    if not (jd / "job.json").is_file():
        make_job(jd, cls="memo")
    seat = seat or seat_with(standard_docs())
    r = DraftingRun(
        jd,
        inputs_dir=str(inputs),
        pricing=str(pricing),
        seat_factory=lambda: seat,
        client=client,
        log=lambda _m: None,
        readback_pause=0.0,
    )
    return r, r.run(), seat


def _reviews(client) -> list[dict]:
    return [c for c in client.calls if "INDEPENDENT PRE-DELIVERY REVIEW" in json.dumps(c.get("system"))]


def _notes(r) -> str:
    from medchron.demand import deliver

    return demand_review.docx_text(next(deliver.out_dir(r.data).glob("* - attorney notes.docx")))


def test_the_false_declaration_still_holds_on_the_audit_model_not_the_drafters(tmp_path, pricing):
    client = ScriptedClient(review=FALSE_DECLARATION)
    _r, v, seat = _run(tmp_path, pricing, client)
    assert v.outcome == "failed" and v.reason.startswith("review_held: ") and seat.sent == []
    assert {c["model"] for c in _reviews(client)} == {"claude-opus-5"}  # audit, never compose


def test_an_authored_review_model_is_used(tmp_path, pricing):
    client = ScriptedClient()
    _r, v, _ = _run(tmp_path, pricing, client, models={**MODELS, "review": "claude-opus-5-5"})
    assert v.outcome == "delivered", (v.stage, v.reason)
    assert {c["model"] for c in _reviews(client)} == {"claude-opus-5-5"}
    assert v.proof["review_model"] == "claude-opus-5-5"


def test_a_clean_draft_is_delivered_with_a_proof_block_the_daemon_believes(tmp_path, pricing):
    r, v, _ = _run(tmp_path, pricing, ScriptedClient())
    assert v.outcome == "delivered", (v.stage, v.reason)
    p = v.proof
    assert p["readback"] == "confirmed" and p["review"] == "passed" and isinstance(p["review_cents"], int)
    manifest = json.loads((r.data / "out" / "demand" / "upload_manifest.json").read_text())
    assert {(f["name"], f["sha256"]) for f in p["files"]} == {(m["name"], m["sha256"]) for m in manifest}
    assert gate.check(v.to_list()) is None


def test_a_release_files_with_the_released_finding_in_the_attorney_notes(tmp_path, pricing):
    client = ScriptedClient(review=FALSE_DECLARATION)
    _run(tmp_path, pricing, client)
    (tmp_path / "job" / "release-review.json").write_text(json.dumps({"reason": "attorney will strike paragraph 3"}))
    r, v, seat = _run(tmp_path, pricing, client)
    assert v.outcome == "delivered", (v.stage, v.reason)
    assert v.proof["review"] == "released" and len(_reviews(client)) == 1  # the review is not paid twice
    assert "MUST-FIX: The declaration swears the set exceeds 35" in _notes(r)
    assert json.loads((r.data / "review.json").read_text())["released_reason"] == "attorney will strike paragraph 3"


def test_a_redo_reviews_again(tmp_path, pricing):
    client = ScriptedClient(review=FALSE_DECLARATION)
    _r, v, seat = _run(tmp_path, pricing, client)
    assert v.outcome == "failed"
    (tmp_path / "job" / demand_review.REDO_MARKER).write_text("{}")
    client.review = CLEAN
    _r, v2, _ = _run(tmp_path, pricing, client, seat=seat)
    assert v2.outcome == "delivered", (v2.stage, v2.reason)
    assert len(_reviews(client)) == 2 and v2.proof["review"] == "passed"


def test_a_must_fix_on_a_check_outside_the_hold_list_rides_into_the_notes(tmp_path, pricing):
    terms = (
        '| MUST-FIX | 4 defined terms | "premises" is used in lower case. | Document 1, item 2 |\n'
        "VERDICT: 1 MUST-FIX, 0 SHOULD-FIX, 0 NOTE"
    )
    r, v, _ = _run(tmp_path, pricing, ScriptedClient(review=terms))
    assert v.outcome == "delivered", (v.stage, v.reason)
    assert 'SHOULD-FIX: "premises" is used in lower case.' in _notes(r)


def test_wrong_bytes_on_the_matter_fail_with_readback_mismatch(tmp_path, pricing):
    _r, v, _ = _run(tmp_path / "probe", pricing, ScriptedClient())  # learn the file name the job will use
    name = next(f["name"] for f in v.files if f["role"] == "attorney_notes")
    seat = seat_with(standard_docs())
    seat.corrupt = {name}  # the vendor stores other bytes of the same length
    _r, v2, _ = _run(tmp_path, pricing, ScriptedClient(), seat=seat)
    assert v2.outcome == "failed" and v2.reason.startswith(f"readback_mismatch: {name}")


def test_the_hold_list_excludes_defined_terms_and_the_catch_all():
    assert 4 not in review.HOLD_CHECKS and 10 not in review.HOLD_CHECKS
    rows = review._rows("| MUST-FIX | 10 anything | A wrong date. | Doc 1 |\nVERDICT: 1 MUST-FIX")
    assert [f.grade for f in rows] == [review.SHOULD]
    rows = review._rows("| MUST-FIX | 1 statements | A false count. | Doc 1 |\nVERDICT: 1 MUST-FIX")
    assert [f.grade for f in rows] == [review.MUST]
