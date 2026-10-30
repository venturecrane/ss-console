"""Proven delivery for the litigation list (2026-10-13): the built workbook is
re-read from its own bytes against the data it was built from before it is
filed, and the filed file is read back by id, size and sha256 after.

Falsifiers: each planted defect is caught and nothing is filed; the clean
fixture files (the run test covers it end to end) with no false hold."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from litigation_testkit import M1, LitSeat, ScriptedLit, make_inputs, make_job, matter_docs
from medchron.litigation import book, review
from medchron.litigation.run import LitigationRun
from medchron.proof import review as proof_review

TODAY = dt.date(2026, 10, 7)


@pytest.fixture(autouse=True)
def _pricing(tmp_path: Path, pricing_path: Path) -> None:
    (tmp_path / "pricing.json").write_text(pricing_path.read_text())


def _run(tmp_path: Path, seat: LitSeat) -> LitigationRun:
    return LitigationRun(
        make_job(tmp_path / "job"),
        inputs_dir=str(make_inputs(tmp_path / "inputs")),
        pricing=str(tmp_path / "pricing.json"),
        state_dir=tmp_path / "state",
        seat_factory=lambda: seat,
        client=ScriptedLit(),
        log=lambda m: None,
        today=lambda: TODAY,
        readback_pause=0.0,
    )


def _matter(number: str, defendant: str = "Delta Corp") -> dict:
    return {
        "matter_id": f"m-{number}",
        "number": number,
        "title": f"Case {number}",
        "responsible": "Alpha Example",
        "case_status": {"value": "Active"},
        "defendants": [{"name": defendant, "status": "Served", "served": {"date": "2026-09-01"}}],
    }


def _book(tmp_path: Path, ms: list[dict]) -> bytes:
    out = tmp_path / "w.xlsx"
    book.build(ms, {}, out, TODAY)
    return out.read_bytes()


# ---- the code review, unit ----------------------------------------------------------------------
def test_a_clean_workbook_has_no_findings(tmp_path):
    ms = [_matter("100001"), _matter("100002", "Zeta LLC")]
    assert review.findings(_book(tmp_path, ms), ms, ms) == []


def test_a_workbook_that_disagrees_with_its_data_is_a_must_fix(tmp_path):
    ms = [_matter("100001")]
    blob = _book(tmp_path, ms)
    drifted = [{**ms[0], "defendants": [{**ms[0]["defendants"][0], "status": "Answered"}]}]
    fs = review.findings(blob, drifted, drifted)
    assert [f.check for f in fs] == ["rows"] and fs[0].grade == proof_review.MUST


def test_a_sent_out_cell_that_disagrees_with_its_data_is_a_must_fix_at_its_column(tmp_path):
    """2026-10-24: the review checks the new columns cell by cell; a sent date
    that never got written, or a read marker the file lost, holds the list."""
    m = _matter("100001")
    m["defendants"][0]["sent_for_service"] = {"date": "2026-08-20", "source": {"name": "Request.msg"}}
    blob = _book(tmp_path, [m])
    assert review.findings(blob, [m], [m]) == []
    unread = {**m, "defendants": [{k: v for k, v in m["defendants"][0].items() if k != "sent_for_service"}]}
    fs = review.findings(blob, [unread], [unread])
    col = book.DEF_HEADER.index("Sent out for service") + 1
    assert [f.check for f in fs] == ["rows"] and f"column {col}," in fs[0].finding


def test_an_in_scope_matter_missing_from_the_list_is_a_must_fix(tmp_path):
    ms = [_matter("100001")]
    fs = review.findings(_book(tmp_path, ms), ms, ms + [_matter("100002")])
    assert any(f.check == "matters" and "100002 has 0 rows" in f.finding for f in fs)


def test_a_matter_listed_twice_is_a_must_fix(tmp_path):
    ms = [_matter("100001")]
    fs = review.findings(_book(tmp_path, ms + ms), ms + ms, ms)
    assert any(f.check == "matters" and "100001 has 2 rows" in f.finding for f in fs)


def test_system_vocabulary_in_a_cell_is_a_must_fix(tmp_path):
    ms = [{**_matter("100001"), "notes": "Status taken from this chunk of the file."}]
    fs = review.findings(_book(tmp_path, ms), ms, ms)
    assert [f.check for f in fs] == ["vocabulary"] and "this chunk" in fs[0].finding


def test_an_unopenable_workbook_is_a_must_fix():
    fs = review.findings(b"not a workbook", [], [])
    assert fs and fs[0].grade == proof_review.MUST


# ---- the run: a hold files nothing; a mismatch fails with its code --------------------------------
def test_a_review_hold_fails_resumable_with_review_held_and_files_nothing(tmp_path, monkeypatch):
    seat = LitSeat({M1: matter_docs()})
    r = _run(tmp_path, seat)
    real = review.findings

    def drifted(xlsx, finals, in_scope, tagged=None):
        finals = [{**m, "defendants": [{**d, "name": "Someone Else"} for d in m["defendants"]]} for m in finals]
        return real(xlsx, finals, in_scope, tagged)

    monkeypatch.setattr(review, "findings", drifted)
    v = r.run()
    assert v.verdict == "failed" and v.reason.startswith("review_held:"), v.reason
    assert v.stage == "review" and seat.sent == []
    assert "proof" not in json.loads(v.to_json())


def test_an_smd_release_files_past_the_hold_and_says_released(tmp_path, monkeypatch):
    seat = LitSeat({M1: matter_docs()})
    r = _run(tmp_path, seat)
    monkeypatch.setattr(review, "findings", lambda *_a: [proof_review.code(proof_review.MUST, "rows", "planted")])
    assert r.run().verdict == "failed"
    (tmp_path / "job" / proof_review.RELEASE_MARKER).write_text(json.dumps({"reason": "checked by hand"}))
    v = _run(tmp_path, seat).run()
    assert v.verdict == "delivered", v.reason
    assert v.proof["review"] == "released" and len(seat.sent) == 3  # the list, its one-sheet-per-case copy, the PDF


def test_a_redo_on_a_still_wrong_workbook_holds_again(tmp_path, monkeypatch):
    """SMD's redo re-runs the code review; it must not forget the record it
    just saved and file the workbook as if no review had run."""
    seat = LitSeat({M1: matter_docs()})
    r = _run(tmp_path, seat)
    monkeypatch.setattr(review, "findings", lambda *_a: [proof_review.code(proof_review.MUST, "rows", "planted")])
    assert r.run().verdict == "failed"
    (tmp_path / "job" / proof_review.REDO_MARKER).write_text("{}")
    v = _run(tmp_path, seat).run()
    assert v.verdict == "failed" and v.reason.startswith("review_held:"), v.reason
    assert seat.sent == []


class WrongBytesSeat(LitSeat):
    """The vendor serves different bytes, at the same length, for what we sent."""

    def mint(self, matter_id: str, file_ids: list[str]) -> list[dict]:
        out = super().mint(matter_id, file_ids)
        for fid in file_ids:
            if fid.startswith("up-"):
                self.blobs[fid] = bytes(b ^ 0xFF for b in self.blobs[fid])
        return out


def test_remote_bytes_that_differ_fail_as_readback_mismatch(tmp_path):
    seat = WrongBytesSeat({M1: matter_docs()})
    v = _run(tmp_path, seat).run()
    assert v.verdict == "failed" and v.reason.startswith("readback_mismatch:"), v.reason
    assert v.stage == "file" and len(seat.sent) == 1
