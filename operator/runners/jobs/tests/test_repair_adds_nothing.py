"""No audit repair adds a word the claim and the finding do not carry
(2026-11-07, repair.added_words). On a client seat a repair of a model-written
sentence supplied a name and a date neither the claim nor the finding had, and
the misattributed sentence reached the firm. Driven through `repair.run` with a
scripted repair tier, the way the audit loop calls it. Invented data."""

from __future__ import annotations

import json
from pathlib import Path

from test_audit import PROSE, Scripted, _exhibit_set, _sr, _write_doc, text_msg

from jobs.medchron.audit import claims as CL, repair
from jobs.medchron.audit.run import AuditPaths

CLAIM = "Physical therapy was continued for the lumbar strain at this visit. (Exhibit 1 - p. 1)"
FINDING = {
    "verdict": "PARTIAL",
    "unsupported_assertions": ["for the lumbar strain"],
    "contradictions": [],
    "note": "the page does not name the condition treated",
}


def _scene(job_dir: Path, firm: Path, data_root: Path, answer: str):
    sr = _sr(job_dir, firm, data_root, Scripted(lambda _p, _n: text_msg(answer)))
    _exhibit_set(sr, [PROSE])
    _write_doc(sr, "01/02/2026\nExample Clinic | Physical Therapy\n\n" + CLAIM)
    paths = AuditPaths(sr.slug_dir, "alpha")
    key = CL.extract_claims(CL.body_of(paths.doc.read_text()), {1})[0]["key"]
    CL.append_row(paths.results, {"key": key, "kind": "real", "exhibit": 1, "page_spec": "1", **FINDING})
    return sr, paths


def _last(paths: AuditPaths) -> dict:
    return [json.loads(x) for x in (paths.out / "repair-edits.jsonl").read_text().splitlines()][-1]


def test_a_repair_that_supplies_a_name_and_a_date_is_refused_and_the_claim_kept(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    """FALSIFIER: on main the rewrite is applied and the new name ships."""
    answer = "Dr. Rivera continued physical therapy on 03/04/2026 at this visit."
    sr, paths = _scene(job_dir, firm_config_path, data_root, answer)
    assert repair.run(sr.doorway, "m", paths, lambda *_: None, pause=0)
    doc = paths.doc.read_text()
    assert "Rivera" not in doc and "lumbar strain" in doc
    row = _last(paths)
    assert row["result"] == "REJECT: added words the claim and the finding do not carry"
    assert row["added"] == ["03/04/2026", "Dr", "Rivera"]


def test_a_repair_that_only_removes_or_hedges_is_applied(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    sr, paths = _scene(
        job_dir, firm_config_path, data_root, "The record notes physical therapy was continued at this visit."
    )
    assert repair.run(sr.doorway, "m", paths, lambda *_: None, pause=0)
    doc = paths.doc.read_text()
    assert "lumbar strain" not in doc and "physical therapy was continued" in doc


def test_words_the_finding_carries_may_be_added() -> None:
    finding = ["the page states a home exercise program"]
    assert (
        repair.added_words("Therapy was continued.", "Therapy and a home exercise program were continued.", finding)
        == []
    )
    assert repair.added_words("Therapy was continued.", "Therapy was continued by Dr. Rivera.", finding) == [
        "Dr",
        "Rivera",
    ]
