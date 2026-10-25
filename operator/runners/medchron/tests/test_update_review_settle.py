"""The update review settles by the chronology review's own rules before it
can hold (2026-10-25, ``stages/update_review_settle.py``).

The update's independent review ran before the shared pre-delivery review and
held on every must-fix its finisher could not correct, while the chronology
lane's review had stopped holding on two classes: the system's own words in
the entries (deleted, ``review_settle.scrub_system``) and a disagreement
between statements the audit confirmed (disclosed or flagged for the attorney,
``review_settle.disclose_differ``). The same functions now run on the update's
open findings. Each rule has its falsifier: the case it must not settle stays
open and the job still holds. Every name and value is synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from medchron.audit import claims as CL
from medchron.stages import review_settle, update_review as UR, update_review_settle as URS
from medchron.update import review_repair as RR
import update_testkit as K

DATE = "08/27/2026"
HEAD = f"{DATE}\nSample Imaging Center | Medical Diagnoses\n\nMedical Diagnoses\n"
MRI = "Lumbar MRI showed a disc protrusion of 4 mm at the fourth and fifth lumbar level. (Exhibit 2 - p. 1)"
SYS = "Per the transcription, the remaining fields on the form were blank. (Exhibit 2 - p. 2)"
PLAN = "The plan was a course of physical therapy twice weekly for the lumbar spine. (Exhibit 2 - p. 3)"
LATER = (
    "09/10/2026\nSample Therapy | All Other Information\n\nAll Other Information\n"
    "Therapy evaluation recorded lumbar flexion limited to forty degrees. (Exhibit 3 - p. 1)"
)


def _scene(tmp_path: Path, entries: list[str], confirmed: bool = True) -> tuple[SimpleNamespace, Path]:
    sd = tmp_path / "matter"
    ud = sd / "runs" / "alpha" / "update"
    ud.mkdir(parents=True)
    out = sd / "out" / "alpha"
    out.mkdir(parents=True)
    for n in (2, 3):
        (out / f"Exhibit {n} - Sample.pdf").write_bytes(b"%PDF")
    (ud / "base.md").write_text(K.base_md())
    md = (
        "Alpha Example - Medical Chronology\n\n## Treatment Timeline\n\n## Diagnostic Highlights\n\n"
        "## Medical Chronology\n\n" + "\n\n".join(entries) + "\n\n## Exhibit List\n"
    )
    doc = sd / "runs" / "alpha" / "final-chronology.md"
    doc.write_text(md)
    if confirmed:  # the audit graded every claim SUPPORTED on its cited page
        rows = [
            {"key": c["key"], "kind": "real", "verdict": "SUPPORTED", "claim": c["claim"]}
            for c in CL.extract_claims(CL.body_of(md), {2, 3})
        ]
        (out / "audit-results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    logged: list[str] = []
    sr = SimpleNamespace(
        slug_dir=sd,
        unit=SimpleNamespace(unit="alpha", client_name="Alpha Example", dob=None),
        job=SimpleNamespace(incident_date=K.INCIDENT, path=sd / "job" / "job.json"),
        cfg=SimpleNamespace(get=lambda *a: {"review": "m"} if a[-1] == "tiers" else sorted(K.HEADINGS)),
        log=logged.append,
        logged=logged,
    )
    return sr, doc


def _f(anchor: str, klass: str, problem: str, page: int = 1, exhibit: int = 2) -> RR.Finding:
    return RR.Finding("MUST", klass, anchor, exhibit, page, problem, "none", "", "")


def _settled(sr: SimpleNamespace) -> list[dict[str, Any]]:
    p = sr.slug_dir / "out" / "alpha" / URS.SETTLED
    return json.loads(p.read_text()) if p.is_file() else []


# ---- system wording ------------------------------------------------------------------
def test_system_wording_in_its_own_cited_paragraph_is_deleted_and_noted(tmp_path: Path) -> None:
    sr, doc = _scene(tmp_path, [HEAD + MRI + "\n\n" + SYS, LATER])
    f = _f("N1.c2", "6 process or system wording", 'the sentence "Per the transcription" is about how it was made', 2)
    rec: dict[str, Any] = {}
    assert URS.settle(sr, doc, [f], rec) == []
    md = doc.read_text()
    assert "Per the transcription" not in md and MRI in md and LATER in md
    assert f.outcome.startswith("settled by rule before delivery: removed before delivery, system wording")
    assert rec["settled_by_rule"][0]["grade"] == "NOTE" and _settled(sr) == rec["settled_by_rule"]
    notes = URS.notes(sr.slug_dir / "out" / "alpha")
    assert notes[1] == f"## {URS.NOTES_HEADING}" and "system wording" in notes[3]


def test_falsifier_system_wording_sharing_a_claim_with_care_is_not_deleted(tmp_path: Path) -> None:
    """Deleting it would change the words of a claim the audit graded, so the
    filing would carry an unaudited sentence: the finding stays open."""
    shared = MRI.replace(" (Exhibit", " Per the transcription, the remaining fields were blank. (Exhibit")
    sr, doc = _scene(tmp_path, [HEAD + shared, LATER])
    before = doc.read_text()
    f = _f("N1.c1", "6 process or system wording", 'the words "Per the transcription" describe the process')
    assert URS.settle(sr, doc, [f], {}) == [f]
    assert doc.read_text() == before and _settled(sr) == []


def test_falsifier_uncited_text_with_nothing_quoted_stays_open(tmp_path: Path) -> None:
    sr, doc = _scene(tmp_path, [HEAD + MRI, LATER])
    f = _f("N1.u1", "6 uncited text", "a paragraph of client text no citation closes")
    assert URS.settle(sr, doc, [f], {}) == [f]


# ---- the records differ ----------------------------------------------------------------
def test_a_value_confirmed_on_its_page_and_read_otherwise_on_another_page_is_disclosed(tmp_path: Path) -> None:
    sr, doc = _scene(tmp_path, [HEAD + MRI + "\n\n" + PLAN, LATER])
    f = _f("N1.c1", "1 wrong value", 'the protrusion is "5 mm" on the later page', page=3)
    rec: dict[str, Any] = {}
    assert URS.settle(sr, doc, [f], rec) == []
    (row,) = rec["settled_by_rule"]
    assert row["grade"] == "NOTE" and row["finding"].startswith(review_settle.DIFFER_NOTE)
    assert "Exhibit 2 p. 1" in row["finding"] and "Exhibit 2 p. 3" in row["finding"]
    assert MRI in doc.read_text()  # disclosure only: nothing in the entries changes


def test_a_disagreement_with_one_side_unconfirmed_is_flagged_for_the_attorney(tmp_path: Path) -> None:
    sr, doc = _scene(tmp_path, [HEAD + MRI, LATER])  # no claim of the entry cites page 3
    f = _f("N1.c1", "1 wrong value", "the protrusion size differs on the operative note", page=3)
    rec: dict[str, Any] = {}
    assert URS.settle(sr, doc, [f], rec) == []
    assert rec["settled_by_rule"][0]["finding"].startswith(review_settle.FLAG_NOTE)


def test_falsifier_a_dispute_about_the_claims_own_page_still_holds(tmp_path: Path) -> None:
    sr, doc = _scene(tmp_path, [HEAD + MRI, LATER])
    f = _f("N1.c1", "1 wrong value", "the protrusion size is misread", page=1)
    assert URS.settle(sr, doc, [f], {}) == [f]


def test_falsifier_a_claim_the_audit_did_not_confirm_still_holds(tmp_path: Path) -> None:
    sr, doc = _scene(tmp_path, [HEAD + MRI, LATER], confirmed=False)
    f = _f("N1.c1", "1 wrong value", "the protrusion size differs on the operative note", page=3)
    assert URS.settle(sr, doc, [f], {}) == [f]


def test_falsifier_an_omission_is_no_class_these_rules_settle(tmp_path: Path) -> None:
    sr, doc = _scene(tmp_path, [HEAD + MRI, LATER])
    f = _f("N1", "2 omitted content", "a plan on page 3 is left out", page=3)
    assert URS.settle(sr, doc, [f], {}) == [f]


# ---- in the stage: before the hold, and again on resume ---------------------------------
def test_the_stage_settles_before_it_holds_and_holds_on_what_no_rule_settles(tmp_path: Path) -> None:
    sr, doc = _scene(tmp_path, [HEAD + MRI + "\n\n" + SYS, LATER])
    ud = sr.slug_dir / "runs" / "alpha" / "update"
    sys_f = _f("N1.c2", "6 process or system wording", 'the words "Per the transcription" are the process', 2)
    assert UR._hold(SimpleNamespace(sr=sr), {"rounds": []}, [sys_f], doc, ud) == 0
    assert json.loads((ud / UR.RECORD).read_text())["status"] == "clean"
    left = _f("N2", "2 omitted content", "a goal on the page is left out", exhibit=3)
    assert UR._hold(SimpleNamespace(sr=sr), {"rounds": []}, [left], doc, ud) == 2
    rec = json.loads((ud / UR.RECORD).read_text())
    assert rec["status"] == "held" and [o["anchor"] for o in rec["open"]] == ["N2"]
    assert sr.logged[-1].startswith("update_held:") and "a goal on the page is left out" in sr.logged[-1]


def test_a_review_held_before_the_rules_settles_on_resume_without_a_model_call(tmp_path: Path) -> None:
    sr, doc = _scene(tmp_path, [HEAD + MRI + "\n\n" + SYS, LATER])
    ud = sr.slug_dir / "runs" / "alpha" / "update"
    sys_f = _f("N1.c2", "6 process or system wording", 'the words "Per the transcription" are the process', 2)
    held = {"status": "held", "open": [sys_f.as_dict()], "doc_sha_out": UR._sha(doc.read_text()), "rounds": []}
    (ud / UR.RECORD).write_text(json.dumps(held))
    sr.doorway = SimpleNamespace(call=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no model call")))
    assert UR.run(sr) == 0  # type: ignore[arg-type]
    rec = json.loads((ud / UR.RECORD).read_text())
    assert rec["status"] == "clean" and rec["open"] == [] and "Per the transcription" not in doc.read_text()
    # A rerun on the settled document is the clean record, not paid again.
    assert UR.run(sr) == 0  # type: ignore[arg-type]
