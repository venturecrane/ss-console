"""SMD finishes an update the independent review held (``medchron.update_decide``).

WHY (2026-10-29). An update held at ``update_review`` with one must-fix the
finisher could not correct (the reviewer read a flowsheet's time rows right;
the drafter and the audit read them backwards, so the audit reverted the
correction). The hold says "SMD finishes it", and SMD had no way to: the root
resume refused the hold, and a hand edit would leave an unaudited sentence
the update proofs refuse. Every name and value here is synthetic.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import test_upload_daemon as ud
import update_testkit as K
from medchron import resume, update_decide as UD
from medchron.audit import claims as CL, coverage as audit_coverage
from medchron.audit.run import AuditPaths
from medchron.resume import MARKER_PREFIX
from medchron.stages import update_review as UR
from medchron.update import review_repair as RR, values as V

JOB = "01M64DBE623E4VXNS2DEBD7YG2"
HOLD = (
    "update_held: the independent review of the update left must-fix findings the finisher could not correct "
    "against the pages; SMD finishes it; last output: update_held: the independent review of the update left "
    "1 must-fix finding(s) the finisher could not correct against the pages; SMD finishes it: entry N1"
)
HEAD = "08/27/2026\nSample Hospital | All Other Information\n\nAll Other Information\n"
WRONG = "The flowsheet recorded a pain score of 8 at 0800 and 3 at 1200. (Exhibit 2 - p. 4)"
RIGHT = "The flowsheet recorded a pain score of 3 at 0800 and 8 at 1200. (Exhibit 2 - p. 4)"
OTHER = "Therapy evaluation recorded lumbar flexion limited to forty degrees. (Exhibit 3 - p. 1)"
LATER = "09/10/2026\nSample Therapy | All Other Information\n\nAll Other Information\n" + OTHER


def _doc(entries: list[str]) -> str:
    return (
        "Alpha Example - Medical Chronology\n\n## Treatment Timeline\n\n## Diagnostic Highlights\n\n"
        "## Medical Chronology\n\n" + "\n\n".join(entries) + "\n\n## Exhibit List\n"
    )


def _seat(tmp_path: Path, job_dir: Path, data_root: Path, *, reason: str = HOLD, open_: Any = None):
    d, broker = ud._daemon(tmp_path)
    broker.rows[JOB] = {"state": "held", "reason": reason}
    jd = d.job_dir(JOB)
    jd.mkdir(parents=True)
    shutil.copy(job_dir / "job.yaml", jd / "job.yaml")
    (jd / "daemon.json").write_text(json.dumps({"claimed": True, "state": "held"}))
    (jd / "envelope.json").write_text(json.dumps(ud._envelope(JOB)))
    sd = data_root / "example-matter"
    rd, out = sd / "runs" / "alpha", sd / "out" / "alpha"
    (rd / "update").mkdir(parents=True)
    out.mkdir(parents=True)
    for n in (2, 3):
        (out / f"Exhibit {n} - Sample.pdf").write_bytes(b"%PDF")
    (rd / "update" / "base.md").write_text(K.base_md())
    md = _doc([HEAD + WRONG, LATER])
    (rd / "final-chronology.md").write_text(md)
    (rd / "entries_scoped_final.md").write_text(HEAD + WRONG + "\n\n" + LATER)
    paths = AuditPaths(sd, "alpha")
    rows = [
        {"key": c["key"], "kind": "real", "verdict": "SUPPORTED", "claim": c["claim"]}
        for c in CL.extract_claims(CL.body_of(md), {2, 3})
    ]
    paths.results.write_text("".join(json.dumps(r) + "\n" for r in rows))
    f = RR.Finding("MUST", "2 wrong time", "N1.c1", 2, 4, "the time rows are read backwards", "fix", "", "")
    rec = {
        "status": "held",
        "doc_sha_out": UD._sha(md),
        "open": [f.as_dict()] if open_ is None else open_,
        "rounds": [{"round": 1, "findings": [f.as_dict()]}],
    }
    (rd / "update" / UD.RECORD).write_text(json.dumps(rec))
    return d, sd, paths


def _plan(d, **kw: Any) -> dict[str, Any]:
    kw.setdefault("corrections", [{"old": WRONG, "new": RIGHT}])
    return UD.plan(d, JOB, by="scott@smd.services", source="vfy_x", reason="time rows read backwards", **kw)


def test_smd_correction_finishes_the_held_update_without_paying_again(tmp_path, job_dir, data_root) -> None:
    d, sd, paths = _seat(tmp_path, job_dir, data_root)
    staged = tmp_path / "correction.json"
    staged.write_text(json.dumps({"corrections": [{"old": WRONG, "new": RIGHT}]}))
    p = _plan(d, corrections=UD.load_corrections(staged))
    UD.apply(d, p, staged)
    assert not staged.exists()  # record text does not outlive the act
    md = paths.doc.read_text()
    assert RIGHT in md and WRONG not in md and OTHER in md
    assert RIGHT in (sd / "runs" / "alpha" / "entries_scoped_final.md").read_text()
    # The verdict gate (and so update_proofs' audit check) passes on the corrected document.
    ok, summary = audit_coverage.check(paths, lambda _m: None)
    assert ok and summary["never"] == 0
    smd_rows = [r for r in CL.read_rows(paths.results) if r.get("mode") == "smd"]
    assert len(smd_rows) == 1 and smd_rows[0]["smd"]["by"] == "scott@smd.services"
    # The review record is settled on this document; the stage reuses it and pays nothing.
    rec = json.loads((sd / "runs" / "alpha" / "update" / UD.RECORD).read_text())
    assert rec["status"] == "clean" and rec["open"] == [] and rec["smd_corrected"]["source"] == "vfy_x"
    assert RR.named_values(rec) == set(V.lost(WRONG, RIGHT))
    logged: list[str] = []
    sr = SimpleNamespace(slug_dir=sd, unit=SimpleNamespace(unit="alpha"), log=logged.append)
    assert UR._reuse(sr, rec, md) == 0 and "SMD corrected" in logged[0]  # type: ignore[arg-type]
    assert UR.projected_usd(sd, "alpha", None, None) == 0.0  # no cap hold for a review it never pays for
    # The daemon re-queues it with no --redo: update_review re-runs (it held), then everything after it.
    marker = json.loads((d.queue / f"{MARKER_PREFIX}{JOB}.json").read_text())
    assert marker["redo"] == [] and "corrected by SMD" in marker["reason"]
    assert resume.take_requests(d) == [JOB]
    assert resume.start_run(d, JOB, ["runner"]) == ["runner"]


def test_a_dry_run_plan_writes_nothing(tmp_path, job_dir, data_root) -> None:
    d, sd, paths = _seat(tmp_path, job_dir, data_root)
    before = paths.doc.read_text()
    p = _plan(d)
    assert len(p["attested"]) == 1
    assert paths.doc.read_text() == before and not (d.queue / f"{MARKER_PREFIX}{JOB}.json").exists()


@pytest.mark.parametrize(
    ("kw", "seat", "needle"),
    [
        ({}, {"reason": "audit coverage: a live claim is not finally SUPPORTED"}, "not held by the update's"),
        ({}, {"reason": "update_held: the update into the delivered chronology cannot go on as asked"}, "not held"),
        ({"corrections": [{"old": "not in the document", "new": "x"}]}, {}, "occurs 0 times"),
        ({"corrections": [{"old": WRONG, "new": RIGHT.replace("p. 4", "p. 5")}]}, {}, "changes a citation"),
        ({"corrections": [{"old": WRONG, "new": RIGHT + "\n\nA new paragraph."}]}, {}, "adds or removes a paragraph"),
        ({"corrections": [{"old": "08/27/2026", "new": "08/28/2026"}]}, {}, "date line"),
        ({"corrections": [{"old": OTHER, "new": OTHER.replace("forty", "thirty")}]}, {}, "no correction touches"),
        ({}, {"open_": []}, "not held with open findings"),
    ],
)
def test_the_command_refuses_what_it_cannot_correct(tmp_path, job_dir, data_root, kw, seat, needle) -> None:
    d, _sd, paths = _seat(tmp_path, job_dir, data_root, **seat)
    before = paths.doc.read_text()
    with pytest.raises(UD.Refused, match=needle):
        _plan(d, **kw)
    assert paths.doc.read_text() == before and not (d.queue / f"{MARKER_PREFIX}{JOB}.json").exists()


def test_falsifier_a_document_edited_since_the_hold_is_refused(tmp_path, job_dir, data_root) -> None:
    d, _sd, paths = _seat(tmp_path, job_dir, data_root)
    paths.doc.write_text(paths.doc.read_text().replace(WRONG, RIGHT))
    with pytest.raises(UD.Refused, match="changed since the review held"):
        _plan(d)


def test_falsifier_delivered_text_is_never_corrected(tmp_path, job_dir, data_root) -> None:
    d, sd, paths = _seat(tmp_path, job_dir, data_root)
    base = sd / "runs" / "alpha" / "update" / "base.md"
    base.write_text(base.read_text() + "\n" + WRONG + "\n")
    with pytest.raises(UD.Refused, match="delivered chronology"):
        _plan(d)


def test_falsifier_a_running_job_is_not_touched(tmp_path, job_dir, data_root) -> None:
    d, _sd, _paths = _seat(tmp_path, job_dir, data_root)
    (d.job_dir(JOB) / "daemon.json").write_text(json.dumps({"claimed": True, "state": "running"}))
    with pytest.raises(UD.Refused, match="does not hold"):
        _plan(d)


def test_hold_prefix_is_the_update_review_stage_hold() -> None:
    src = (Path(UD.__file__).parent / "dag_update.py").read_text(encoding="utf-8")
    stage = src[src.index('        "update_review",\n') :]
    assert f'"{UD.HOLD_PREFIX} ' in stage[: stage.index("    Stage(")]
    from medchron.stages import update_review_settle as URS

    assert URS.hold_line([]).startswith(UD.HOLD_PREFIX)


def test_a_value_the_correction_takes_out_is_named_for_proof_10(tmp_path, job_dir, data_root) -> None:
    d, sd, _paths = _seat(tmp_path, job_dir, data_root)
    fixed = WRONG.replace("score of 8", "score of 6")
    assert V.lost(WRONG, fixed)  # the falsifier: this edit does take a value out
    UD.apply(d, _plan(d, corrections=[{"old": WRONG, "new": fixed}]))
    rec = json.loads((sd / "runs" / "alpha" / "update" / UD.RECORD).read_text())
    assert set(V.lost(WRONG, fixed)) <= RR.named_values(rec)
