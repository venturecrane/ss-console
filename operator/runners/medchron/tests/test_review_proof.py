"""Proven delivery for the chronology (2026-10-13): the pre-delivery review
holds what an attorney must not receive, and the upload is believed only when
its bytes read back.

Every check is a falsifier pair: a planted defect is CAUGHT, and the clean
package PASSES (a review that holds legitimate work is the next false-positive
gate, which is the defect class it must not become).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from medchron import config as config_mod, job as job_mod, unit_proof, wording as W
from medchron.proof import review as proof_review
from medchron.stages import manifest, render, review, upload
from medchron.stages.base import StageRun
from medchron_testkit import FakeSeat, make_pdf
from test_decisions_driver import _NoNetwork, _driver, _seat, _Stream, _text_msg
from test_final_stages import DOC
from test_upload_daemon import _upload_sr

CLEAN = "VERDICT: 0 MUST-FIX, 0 SHOULD-FIX, 0 NOTE"
TODAY = date(2026, 10, 13)


class FakeDoorway:
    """The paid doorway, scripted: each call answers the next text."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.calls: list[dict] = []

    def call(self, stage, **kw):
        self.calls.append({"stage": stage, **kw})
        text = self.answers.pop(0) if self.answers else CLEAN
        return SimpleNamespace(text=text, stop_reason="end_turn", usage=None)


def _package(job_dir: Path, firm: Path, data_root: Path, doc: str = DOC) -> tuple[StageRun, list[str]]:
    """The chronology rendered from `doc` and its manifest, as the review sees
    them in a real run."""
    job = job_mod.load(job_dir)
    log: list[str] = []
    sr = StageRun(
        job=job,
        cfg=config_mod.load(str(firm)),
        unit=job.units[0],
        slug_dir=data_root / "example-matter",
        decided={},
        log=log.append,
        seat_factory=lambda: FakeSeat([], [], {}),
        date_stamp="10-13-26",
    )
    rd = sr.slug_dir / "runs" / "alpha"
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "final-chronology.md").write_text(doc)
    out = sr.slug_dir / "out" / "alpha"
    out.mkdir(parents=True, exist_ok=True)
    (out / "Exhibit 1 - Example Clinic - 01-20-2026 (Medical Records).pdf").write_bytes(make_pdf(["record"]))
    assert render.run(sr) == 0
    assert manifest.run(sr) == 0
    return sr, log


def _review(sr: StageRun, doorway: FakeDoorway, today: date = TODAY) -> int:
    sr._doorway = doorway  # type: ignore[assignment]
    return review.run(sr, today=today)


def _manifest_names(sr: StageRun) -> list[str]:
    return [m["name"] for m in json.loads((sr.slug_dir / "out" / "alpha" / "upload_manifest.json").read_text())]


# ---- the review ------------------------------------------------------------------


def test_a_clean_chronology_passes_on_the_strongest_tier_with_no_notes_file(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root)
    d = FakeDoorway(CLEAN)
    assert _review(sr, d) == 0
    rec = proof_review.load(sr.slug_dir / "runs" / "alpha")
    assert proof_review.status(rec) == "passed" and rec["findings"] == []
    assert d.calls[0]["stage"] == "review" and d.calls[0]["model"] == sr.cfg.get("models", "tiers")["judgment"]
    assert not any("Review Notes" in n for n in _manifest_names(sr))
    assert (
        "Alpha Example" in d.calls[0]["messages"][0]["content"]
        and "Cervical strain." in d.calls[0]["messages"][0]["content"]
    )


def test_this_chunk_in_the_chronology_holds(job_dir, firm_config_path, data_root):
    """eng#226: "this chunk" reached a firm in a delivered chronology."""
    doc = DOC.replace("Continue therapy.", "Continue therapy as noted in this chunk.")
    sr, log = _package(job_dir, firm_config_path, data_root, doc)
    assert _review(sr, FakeDoorway(CLEAN)) == 2
    assert log[-1].startswith("review_held:") and "this chunk" in log[-1]


def test_an_entry_dated_after_today_holds(job_dir, firm_config_path, data_root):
    """A record cannot be from the future. The DOC's last entry is 03/30/2026;
    reviewed as of 2026-02-01 it is in the future."""
    sr, log = _package(job_dir, firm_config_path, data_root)
    assert _review(sr, FakeDoorway(CLEAN), today=date(2026, 2, 1)) == 2
    assert log[-1].startswith("review_held:") and "03/30/2026" in log[-1]


def test_a_citation_to_an_exhibit_not_delivered_holds(job_dir, firm_config_path, data_root):
    doc = DOC.replace("Continue therapy. (Exhibit 1 - p. 3)", "Continue therapy. (Exhibit 2 - p. 1)")
    sr, log = _package(job_dir, firm_config_path, data_root, doc)
    assert _review(sr, FakeDoorway(CLEAN)) == 2
    assert "Exhibit 2" in log[-1]


def test_a_model_must_fix_on_a_hold_check_holds(job_dir, firm_config_path, data_root):
    """A model MUST-FIX on a hold check the policy cannot settle holds (a
    check 4 whose quote names no single line and no entry)."""
    sr, log = _package(job_dir, firm_config_path, data_root)
    row = (
        '| MUST-FIX | 4 summary with no entry | the reference "Exhibit 1 - p. 2" has no support | Treatment Timeline |'
    )
    assert _review(sr, FakeDoorway(f"{row}\nVERDICT: 1 MUST-FIX, 0 SHOULD-FIX, 0 NOTE")) == 2
    assert log[-1].startswith("review_held:") and "no support" in log[-1]


def test_a_co_plaintiffs_visit_is_left_out_by_rule_not_held(job_dir, firm_config_path, data_root):
    """ss#2893: a co-plaintiff's visit inside the client's chronology. Since
    2026-10-24 it is decided on its page (``review_evidence``): the cited
    page does not identify the client, so the entry is left out and
    disclosed, and the job is not held."""
    sr, log = _package(job_dir, firm_config_path, data_root)
    row = "| MUST-FIX | 1 another person's records | the 03/30/2026 visit is the co-plaintiff's | entry 03/30/2026 Example Clinic |"
    assert _review(sr, FakeDoorway(f"{row}\nVERDICT: 1 MUST-FIX, 0 SHOULD-FIX, 0 NOTE")) == 0, log
    assert "03/30/2026\nExample Clinic" not in (sr.slug_dir / "runs" / "alpha" / "final-chronology.md").read_text()


def test_a_model_must_fix_on_a_non_hold_check_is_downgraded_into_the_notes(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root)
    row = "| MUST-FIX | 5 embarrassing | a duplicated sentence in the 01/20/2026 entry | entry 01/20/2026 |"
    assert _review(sr, FakeDoorway(f"{row}\nVERDICT: 1 MUST-FIX, 0 SHOULD-FIX, 0 NOTE")) == 0
    rec = proof_review.load(sr.slug_dir / "runs" / "alpha")
    assert rec["must_fix"] == 0 and rec["should_fix"] == 1
    names = _manifest_names(sr)
    assert names[1] == "Alpha Example - Chronology Review Notes 10-13-26.docx", names
    assert "duplicated sentence" in review.docx_text(sr.slug_dir / "out" / "alpha" / names[1])


# ---- the review's own policy (2026-10-23: stages/review_policy.py) ---------------
def _verdict(*rows: str) -> str:
    must = sum("| MUST-FIX |" in r for r in rows)
    return "\n".join([*rows, f"VERDICT: {must} MUST-FIX, 0 SHOULD-FIX, 0 NOTE"])


def _settle_review(sr: StageRun, doorway: FakeDoorway, verify=None, audit=None) -> int:
    sr._doorway = doorway  # type: ignore[assignment]
    return review.run(
        sr,
        today=TODAY,
        verify=verify or (lambda _b, _a: []),
        images=lambda _x, _p: None,
        audit=audit or (lambda _sr: 0),
    )


def test_a_check3_finding_on_the_firms_required_wording_is_dropped_and_files(job_dir, firm_config_path, data_root):
    """Curtis (3CH59XBY): the review held on "(provider not stated in the
    record)", which the map prompt orders when no page names the clinician."""
    doc = DOC.replace(
        "Example Clinic | Treatment Recommendations",
        f"Example Clinic {W.PROVIDER_NOT_STATED} | Treatment Recommendations",
    )
    sr, log = _package(job_dir, firm_config_path, data_root, doc)
    row = f'| MUST-FIX | 3 system language | "{W.PROVIDER_NOT_STATED}" is a placeholder | entry 03/30/2026 |'
    assert _settle_review(sr, FakeDoorway(_verdict(row))) == 0
    rec = proof_review.load(sr.slug_dir / "runs" / "alpha")
    assert rec["findings"] == [] and any("required wording dropped" in ln for ln in log)
    assert not any("Review Notes" in n for n in _manifest_names(sr))


def test_a_check2_on_the_records_differ_sentence_is_a_note_and_files(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root)
    row = (
        f'| MUST-FIX | 2 entries contradict | the 01/20/2026 entry gives two pain scores and "{W.RECORDS_DIFFER}" '
        "| entry 01/20/2026 Example Clinic |"
    )
    assert _settle_review(sr, FakeDoorway(_verdict(row))) == 0
    rec = proof_review.load(sr.slug_dir / "runs" / "alpha")
    assert rec["must_fix"] == 0 and rec["notes"] == 1
    assert any("Review Notes" in n for n in _manifest_names(sr))


def test_a_check4_summary_line_no_entry_supports_is_deleted_and_re_rendered(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root)
    chron = sr.slug_dir / "out" / "alpha" / "Alpha Example - Medical Chronology 10-13-26.docx"
    assert "Sprain of ligaments of cervical spine" in review.docx_text(chron)
    row = (
        '| MUST-FIX | 4 summary with no entry | the highlight "Sprain of ligaments of cervical spine" has no entry '
        "behind it | Diagnostic Highlights |"
    )
    assert _settle_review(sr, FakeDoorway(_verdict(row))) == 0, log
    md = (sr.slug_dir / "runs" / "alpha" / "final-chronology.md").read_text()
    assert "Sprain of ligaments of cervical spine" not in md and "Cervical strain." in md
    assert "Sprain of ligaments of cervical spine" not in review.docx_text(chron)
    rec = proof_review.load(sr.slug_dir / "runs" / "alpha")
    assert rec["must_fix"] == 0 and "removed before delivery" in rec["findings"][0]["finding"]
    # The recorded input sha is the re-rendered package's: a resume does not pay again.
    d = FakeDoorway()
    assert _settle_review(sr, d) == 0 and d.calls == []


def test_a_check4_quote_that_names_no_single_summary_line_holds(job_dir, firm_config_path, data_root):
    """Deletion only when the quote names exactly one line; otherwise MUST,
    and a finding that names no entry cannot be repaired either."""
    sr, log = _package(job_dir, firm_config_path, data_root)
    row = (
        '| MUST-FIX | 4 summary with no entry | the reference "Exhibit 1 - p. 2" has no support | Treatment Timeline |'
    )
    assert _settle_review(sr, FakeDoorway(_verdict(row))) == 2
    assert log[-1].startswith("review_held:")
    assert "| Example Clinic | 01/20/2026" in (sr.slug_dir / "runs" / "alpha" / "final-chronology.md").read_text()


FINISH = (
    '{"edits": [{"finding": "F1", "anchor": "N2.c1", "action": "replace", '
    '"corrected": "Continue physical therapy three times weekly for six weeks. (Exhibit 1 - p. 3)", '
    '"exhibit": 1, "page": 3, "reason": "the page states the plan"}], "complete": true}'
)
#: The 03/30 plan as a claim the audit grades (a value-free fragment under
#: ``claims.MIN_CHARS`` is no claim, so no finisher edit can anchor to it).
PLAN = "Continue physical therapy twice weekly for four weeks."
PLAN_DOC = DOC.replace("Continue therapy.", PLAN)
CONTRA = (
    "| MUST-FIX | 2 entries contradict | the plan on 03/30/2026 contradicts the 01/20/2026 entry "
    "| entry 03/30/2026 Example Clinic, Exhibit 1 p. 3 |"
)


def test_a_check2_must_is_repaired_once_by_the_finisher_audited_and_filed(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root, PLAN_DOC)
    audited: list[str] = []
    d = FakeDoorway(_verdict(CONTRA), FINISH)
    assert _settle_review(sr, d, audit=lambda s: audited.append("audit") or 0) == 0, log
    assert d.calls[1]["stage"] == "update_review" and audited == ["audit"]
    md = (sr.slug_dir / "runs" / "alpha" / "final-chronology.md").read_text()
    assert "three times weekly" in md and PLAN not in md
    chron = sr.slug_dir / "out" / "alpha" / "Alpha Example - Medical Chronology 10-13-26.docx"
    assert "three times weekly" in review.docx_text(chron)
    rec = proof_review.load(sr.slug_dir / "runs" / "alpha")
    assert rec["must_fix"] == 0 and rec["findings"][0]["finding"].startswith("corrected before delivery")
    names = _manifest_names(sr)
    assert "corrected before delivery" in review.docx_text(sr.slug_dir / "out" / "alpha" / names[1])


def test_a_repair_the_audit_does_not_support_is_reverted_and_holds(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root, PLAN_DOC)
    unsupported = [{"claim": "Continue physical therapy three times weekly for six weeks."}]
    rc = _settle_review(sr, FakeDoorway(_verdict(CONTRA), FINISH), verify=lambda _b, _a: unsupported)
    assert rc == 2 and log[-1].startswith("review_held:")
    assert "three times weekly" not in (sr.slug_dir / "runs" / "alpha" / "final-chronology.md").read_text()


def test_a_finisher_that_declines_leaves_the_must_and_holds(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root, PLAN_DOC)
    decline = (
        '{"edits": [{"finding": "F1", "anchor": "N2", "action": "decline", '
        '"reason": "the page does not show it"}], "complete": true}'
    )
    assert _settle_review(sr, FakeDoorway(_verdict(CONTRA), decline)) == 2
    assert log[-1].startswith("review_held:") and "contradicts" in log[-1]


def test_a_check1_must_is_never_repaired(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root)
    row = (
        "| MUST-FIX | 1 another person's records | the 03/30/2026 visit is the co-plaintiff's "
        "| entry 03/30/2026 Example Clinic, Exhibit 1 p. 3 |"
    )
    d = FakeDoorway(_verdict(row), FINISH)
    # Decided on its page, never repaired (2026-10-24): the entry is left out.
    assert _settle_review(sr, d) == 0 and len(d.calls) == 1
    assert "Continue therapy." not in (sr.slug_dir / "runs" / "alpha" / "final-chronology.md").read_text()


def test_an_audit_gate_drop_is_listed_in_the_review_notes(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root)
    out = sr.slug_dir / "out" / "alpha"
    claims = [
        {"how": "dropped at the audit gate", "exhibit": 1, "page_spec": "2", "claim": "Range of motion was full."},
        {"how": "dropped at the round cap", "exhibit": 1, "page_spec": "3", "claim": "Not listed here."},
    ]
    (out / "audit-dropped-claims.json").write_text(json.dumps({"unit": "alpha", "count": 2, "claims": claims}))
    assert _settle_review(sr, FakeDoorway(CLEAN)) == 0
    names = _manifest_names(sr)
    text = review.docx_text(out / names[1])
    assert "Range of motion was full." in text and "Exhibit 1, p. 2" in text and "Not listed here." not in text


def test_a_held_review_is_not_paid_twice_and_smds_release_files_it_with_the_findings(
    job_dir, firm_config_path, data_root
):
    doc = DOC.replace("Continue therapy.", "Continue therapy as noted in this chunk.")
    sr, log = _package(job_dir, firm_config_path, data_root, doc)
    d = FakeDoorway(CLEAN)
    assert _review(sr, d) == 2
    assert _review(sr, d) == 2 and len(d.calls) == 1, "a resume holds again from the record, unpaid"
    (job_dir / proof_review.RELEASE_MARKER).write_text(json.dumps({"reason": "attorney asked for it as is"}))
    assert _review(sr, d) == 0 and len(d.calls) == 1
    rec = proof_review.load(sr.slug_dir / "runs" / "alpha")
    assert proof_review.status(rec) == "released" and rec["released_reason"] == "attorney asked for it as is"
    notes = review.docx_text(sr.slug_dir / "out" / "alpha" / _manifest_names(sr)[1])
    assert "MUST-FIX" in notes and "this chunk" in notes


def test_a_redo_marker_reviews_afresh_once_per_request(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root)
    row = (
        '| MUST-FIX | 4 summary with no entry | the reference "Exhibit 1 - p. 2" has no support | Treatment Timeline |'
    )
    d = FakeDoorway(f"{row}\nVERDICT: 1 MUST-FIX, 0 SHOULD-FIX, 0 NOTE", CLEAN)
    assert _review(sr, d) == 2
    (job_dir / proof_review.REDO_MARKER).write_text("{}")
    assert _review(sr, d) == 0 and len(d.calls) == 2, "the redo paid for a fresh review"
    assert _review(sr, d) == 0 and len(d.calls) == 2, "the same redo request is not re-applied"
    assert (job_dir / proof_review.REDO_MARKER).is_file(), "left for the next unit of a joint matter"


def test_a_changed_package_is_reviewed_again(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root)
    d = FakeDoorway(CLEAN, CLEAN)
    assert _review(sr, d) == 0
    (sr.slug_dir / "out" / "alpha" / "Exhibit 1 - Example Clinic - 01-20-2026 (Medical Records).pdf").write_bytes(
        make_pdf(["another"])
    )
    assert manifest.run(sr) == 0
    assert _review(sr, d) == 0 and len(d.calls) == 2


def test_a_review_that_never_finishes_fails_and_files_nothing(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root)
    assert _review(sr, FakeDoorway("| NOTE | 5 x | y | z |", "| NOTE | 5 x | y | z |")) == 1
    assert proof_review.load(sr.slug_dir / "runs" / "alpha") is None


def test_words_a_record_may_contain_are_should_fix_never_a_hold():
    found = review.code_findings(
        "Billed by code 99213. Note continued in this chunk.", [("01/20/2026", "Clinic")], [], {}, today=TODAY, dob=None
    )
    grades = {f.finding.split('"')[1]: f.grade for f in found}
    assert grades["by code"] == proof_review.SHOULD and grades["in this chunk"] == proof_review.MUST


def test_an_entry_before_birth_is_should_fix_and_an_empty_chronology_must_fix():
    before = review.code_findings("x", [("01/20/1960", "Clinic")], [], {}, today=TODAY, dob=date(1970, 1, 1))
    assert [(f.grade, f.check) for f in before] == [(proof_review.SHOULD, "5 before birth")]
    empty = review.code_findings("x", [], [], {}, today=TODAY, dob=None)
    assert [f.grade for f in empty] == [proof_review.MUST]


def test_a_placeholder_exhibit_title_is_must_fix():
    """ss#2819: "Provider not identified in this chunk" as an exhibit title."""
    name = "Exhibit 3 - Provider not identified in this chunk - 01-20-2026 (Medical Records).pdf"
    found = review.code_findings("x", [("01/20/2026", "Clinic")], [name], {}, today=TODAY, dob=None)
    assert any(f.grade == proof_review.MUST and name in f.cite for f in found)


# ---- the read-back by hash -------------------------------------------------------


def test_upload_reads_back_by_hash_and_marks_every_row_proven(job_dir, firm_config_path, data_root):
    seat = FakeSeat([], [], {})
    log: list[str] = []
    sr = _upload_sr(job_dir, firm_config_path, data_root, seat, log)
    assert upload.run(sr, pause=0, tries=3) == 0
    d = json.loads((sr.slug_dir / "runs" / "alpha" / "delivery.json").read_text())
    assert all(f["proven"] and f["file_id"].startswith("up-") and f["confirmed"] and f["sent"] for f in d["files"])


def test_upload_with_other_bytes_at_the_same_size_fails_readback_mismatch(job_dir, firm_config_path, data_root):
    """Name and size agree; the bytes do not. Before the hash read-back this was
    `delivered`."""
    seat = FakeSeat([], [], {})
    real_add = seat.add_file

    def swapped(matter_id, folder_id, name, data):
        r = real_add(matter_id, folder_id, name, data)
        if name.startswith("Exhibit 1"):
            seat.blobs[f"up-{len(seat.sent)}"] = b"X" * len(data)
        return r

    seat.add_file = swapped  # type: ignore[method-assign]
    log: list[str] = []
    sr = _upload_sr(job_dir, firm_config_path, data_root, seat, log)
    assert upload.run(sr, pause=0, tries=3) == 1
    assert log[-1].startswith("readback_mismatch:") and "Exhibit 1" in log[-1]
    d = json.loads((sr.slug_dir / "runs" / "alpha" / "delivery.json").read_text())
    assert not any(f.get("proven") for f in d["files"])


# ---- the driver's outcome ---------------------------------------------------------


@pytest.fixture
def _whole(tmp_path, data_root):
    from medchron_testkit import job_yaml

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "job.yaml").write_text(job_yaml(data_root, install_root=tmp_path / "install"), encoding="utf-8")
    return job_dir


@pytest.mark.usefixtures("fake_pipeline")
def test_a_delivered_unit_carries_a_confirmed_proof_block(_whole, firm_config_path, pricing_path):
    o = _driver(_whole, firm_config_path, pricing_path).run()[0]
    assert o.outcome == "delivered", o
    p = o.to_dict()["proof"]
    assert p["readback"] == "confirmed" and p["review"] == "passed" and p["review_model"]
    assert len(p["files"]) == len(o.files) and all(f["file_id"] and len(f["sha256"]) == 64 for f in p["files"])
    assert isinstance(p["review_cents"], int)


@pytest.mark.usefixtures("fake_pipeline")
def test_unproven_rows_never_claim_confirmed(_whole, firm_config_path, pricing_path, data_root):
    d = _driver(_whole, firm_config_path, pricing_path)
    o = d.run()[0]
    files = [{k: v for k, v in f.items() if k != "proven"} for f in o.files]
    rd = d.slug_dir / "runs" / "alpha"
    assert unit_proof.block(rd, o.files, d.pricing)["readback"] == "confirmed"
    assert unit_proof.block(rd, files, d.pricing)["readback"] == "unproven"
    assert unit_proof.block(rd, [], d.pricing)["readback"] == "unproven", "nothing filed is not a delivery"


class _HoldingReviewer(_NoNetwork):
    def stream(self, **kw):
        if "INDEPENDENT PRE-DELIVERY REVIEW" in self._system(kw):
            self.calls.append(kw)
            row = "| MUST-FIX | 3 system language | a heading reads as the system's | Medical Summary |"
            return _Stream(_text_msg(f"{row}\nVERDICT: 1 MUST-FIX, 0 SHOULD-FIX, 0 NOTE"))
        return super().stream(**kw)


@pytest.mark.usefixtures("fake_pipeline")
def test_a_review_hold_ends_the_unit_held_with_a_paged_reason_and_nothing_filed(_whole, firm_config_path, pricing_path):
    seat = _seat()
    o = _driver(_whole, firm_config_path, pricing_path, client=_HoldingReviewer(), seat_factory=lambda: seat).run()[0]
    assert o.outcome == "held" and o.stage == "review" and o.reason.startswith("review_held:"), o
    assert not seat.created and not seat.sent and o.proof is None
