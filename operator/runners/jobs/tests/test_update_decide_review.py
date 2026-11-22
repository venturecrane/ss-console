"""SMD corrects an update the PRE-DELIVERY review held (``update_decide_review``).

WHY (2026-11-22). An update passed its own review, ran its final set and built
its package, then the pre-delivery review held on a sentence its page
contradicts. The only ways on were releasing the wrong sentence or reviewing
the same text again. Every name and value here is synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import test_update_decide as T
from jobs import resume
from jobs.medchron import update_decide as UD, update_decide_review as UDR
from jobs.medchron.audit import claims as CL
from jobs.medchron.update import final_set as FS, review_repair as RR, values as V
from jobs.proof import review as PR
from jobs.resume import MARKER_PREFIX
from test_update_e2e import scene  # noqa: F401 - a pytest fixture the rehearsal test requests by name

REVIEW_HOLD = "review_held: the pre-delivery review found 1 must-fix finding(s); nothing was filed: 1 accuracy: x"


def _review_seat(tmp_path, job_dir, data_root, *, reason: str = REVIEW_HOLD):
    """The update review settled clean, the final set's marker naming the
    document, and the pre-delivery review held on it."""
    d, sd, paths = T._seat(tmp_path, job_dir, data_root, reason=reason)
    rd = sd / "runs" / "alpha"
    md = paths.doc.read_text()
    rec_p = rd / "update" / UD.RECORD
    rec = json.loads(rec_p.read_text())
    rec |= {"status": "clean", "open": [], "doc_sha_out": UD._sha(md)}
    rec_p.write_text(json.dumps(rec))
    mark = {"counts": {}, "ops": [], "in_sha": UD._sha(md), "out_sha": UD._sha(md)}
    (rd / "update" / FS.REPORT).write_text(json.dumps(mark))
    PR.save(rd, [PR.code(PR.MUST, "1 accuracy", "the pain scores are reversed against the page")])
    return d, sd, paths


def _plan(d, **kw: Any) -> dict[str, Any]:
    kw.setdefault("corrections", [{"old": T.WRONG, "new": T.RIGHT}])
    return UDR.route_plan(d, T.JOB, by="scott@smd.services", source="vfy_x", reason="time rows read backwards", **kw)


def test_a_review_held_update_takes_a_words_only_correction_and_reopens_what_reads_it(
    tmp_path, job_dir, data_root
) -> None:
    d, sd, paths = _review_seat(tmp_path, job_dir, data_root)
    staged = tmp_path / "correction.json"
    staged.write_text(json.dumps({"corrections": [{"old": T.WRONG, "new": T.RIGHT}]}))
    p = _plan(d, corrections=UD.load_corrections(staged))
    assert p["mode"] == UDR.MODE and len(p["attested"]) == 1
    UDR.route_apply(d, p, staged)
    assert not staged.exists()
    md = paths.doc.read_text()
    assert T.RIGHT in md and T.WRONG not in md and T.OTHER in md
    rows = [r for r in CL.read_rows(paths.results) if r.get("mode") == "smd"]
    assert len(rows) == 1 and rows[0]["verdict"] == "SUPPORTED" and rows[0]["smd"]["source"] == "vfy_x"
    # The update review stays settled, now on the corrected document, and names what the edit took out.
    rec = json.loads((sd / "runs" / "alpha" / "update" / UD.RECORD).read_text())
    assert rec["status"] == "clean" and rec["doc_sha_out"] == UD._sha(md)
    assert RR.named_values(rec) == set(V.lost(T.WRONG, T.RIGHT))
    # The final set reads the corrected document afresh: its marker no longer names it.
    from jobs.medchron.update import final_set_io as FIO

    assert FIO.source(sd, "alpha") == md and not FIO.ran(sd / "runs" / "alpha" / "update")
    marker = json.loads((d.queue / f"{MARKER_PREFIX}{T.JOB}.json").read_text())
    assert marker["redo"] == list(UDR.REDO) and "pre-delivery review" in marker["reason"]
    # Each reopened stage is a stage of the update's DAG; ``review`` travels as the redo marker.
    from jobs.medchron import dag

    names = dag.by_name(True)
    assert all(n in names and not names[n].paid for n in UDR.REDO if n != "review")
    assert resume.take_requests(d) == [T.JOB]
    argv = resume.start_run(d, T.JOB, ["runner"])
    assert argv == ["runner", "--redo", ",".join(n for n in UDR.REDO if n != "review")]
    assert (d.job_dir(T.JOB) / PR.REDO_MARKER).is_file()  # the review forgets its held record and reviews afresh


@pytest.mark.parametrize(
    ("kw", "needle"),
    [
        ({"corrections": [{"old": T.WRONG, "new": T.RIGHT.replace("p. 4", "p. 5")}]}, "changes a citation"),
        ({"corrections": [{"old": T.WRONG, "new": T.RIGHT + "\n\nNew finding. (Exhibit 9 - p. 1)"}]}, "does not cite"),
        ({"corrections": [{"old": "08/27/2026", "new": "08/28/2026"}]}, "date line"),
        ({"corrections": [{"old": "not in the document", "new": "x"}]}, "occurs 0 times"),
        ({"closures": [UD._closure(0, T.TO_N2)]}, "corrections only"),
        ({"repeats": [{"entry": "e", "paragraph": "p", "why": "w"}]}, "corrections only"),
        ({"confirmed": [{"key": "k", "why": "w"}]}, "corrections only"),
    ],
)
def test_the_review_mode_refuses_what_the_rules_refuse(tmp_path, job_dir, data_root, kw, needle) -> None:
    d, _sd, paths = _review_seat(tmp_path, job_dir, data_root)
    before = paths.doc.read_text()
    with pytest.raises(UD.Refused, match=needle):
        _plan(d, **kw)
    assert paths.doc.read_text() == before and not (d.queue / f"{MARKER_PREFIX}{T.JOB}.json").exists()


@pytest.mark.parametrize(
    ("reason", "needle"),
    [
        ("review incomplete (infrastructure): the model returned no verdict", "not held by the update's"),
        ("update_held: the update into the delivered chronology cannot go on as asked", "not held by the update's"),
        ("manifest: a required deliverable is missing", "not held by the update's"),
    ],
)
def test_a_job_held_for_any_other_reason_is_refused(tmp_path, job_dir, data_root, reason, needle) -> None:
    d, _sd, _paths = _review_seat(tmp_path, job_dir, data_root, reason=reason)
    with pytest.raises(UD.Refused, match=needle):
        _plan(d)


def test_falsifier_the_document_edited_after_the_final_set_is_refused(tmp_path, job_dir, data_root) -> None:
    d, _sd, paths = _review_seat(tmp_path, job_dir, data_root)
    paths.doc.write_text(paths.doc.read_text().replace(T.OTHER, T.OTHER.replace("forty", "thirty")))
    with pytest.raises(UD.Refused, match="not the one the final set wrote"):
        _plan(d)


def test_falsifier_a_passed_review_is_not_corrected_here(tmp_path, job_dir, data_root) -> None:
    d, sd, _paths = _review_seat(tmp_path, job_dir, data_root)
    PR.save(sd / "runs" / "alpha", [])
    with pytest.raises(UD.Refused, match="passed, not held"):
        _plan(d)


def test_falsifier_a_package_recorded_sent_is_refused(tmp_path, job_dir, data_root) -> None:
    d, sd, _paths = _review_seat(tmp_path, job_dir, data_root)
    out, rd = sd / "out" / "alpha", sd / "runs" / "alpha"
    (out / "upload_manifest.json").write_text(json.dumps([{"name": "Alpha Example - Medical Chronology.docx"}]))
    (rd / "delivery.json").write_text(
        json.dumps({"files": [{"name": "Alpha Example - Medical Chronology.docx", "sent": True}]})
    )
    with pytest.raises(UD.Refused, match="recorded sent"):
        _plan(d)


def test_falsifier_a_running_job_is_not_touched(tmp_path, job_dir, data_root) -> None:
    d, _sd, _paths = _review_seat(tmp_path, job_dir, data_root)
    (d.job_dir(T.JOB) / "daemon.json").write_text(json.dumps({"claimed": True, "state": "running"}))
    with pytest.raises(UD.Refused, match="does not hold"):
        _plan(d)


def test_the_hold_prefix_is_the_pre_delivery_review_stage_hold() -> None:
    from jobs.stages import review as RV

    assert RV.EXITS[RV.EXIT_HELD][1].startswith(UDR.REVIEW_PREFIX)
    assert PR.held_reason({"findings": []}).startswith(UDR.REVIEW_PREFIX)


def test_the_update_review_mode_is_unchanged_on_its_own_hold(tmp_path, job_dir, data_root) -> None:
    d, _sd, _paths = T._seat(tmp_path, job_dir, data_root)
    p = UDR.route_plan(d, T.JOB, corrections=[{"old": T.WRONG, "new": T.RIGHT}], by="a", source="b", reason="c")
    assert "mode" not in p and p["marker"]["redo"] == []
    UDR.route_apply(d, p)
    assert json.loads((d.queue / f"{MARKER_PREFIX}{T.JOB}.json").read_text())["redo"] == []


def _held_at_review(scene, data_root, firm, pricing, monkeypatch) -> tuple[Path, Path, str, str]:
    """A delivered update turned back into one the pre-delivery review held:
    every stage before ``review`` done, its record held, one added sentence
    to correct."""
    from jobs.medchron.state import RunState
    from test_update_e2e import _driver, _Model, _upload_pause

    job_dir, seat = scene
    _upload_pause(monkeypatch)
    assert _driver(job_dir, firm, pricing, seat, _Model()).run()[0].outcome == "delivered"
    sd = data_root / "example-matter"
    rd = sd / "runs" / "alpha"
    st = RunState.load_or_new(rd / "state.json", slug="example-matter", unit="alpha")
    st.stages["review"].status, st.stages["upload"].status = "held", "pending"
    st.outcome = st.outcome_reason = None
    st.save()
    (rd / "delivery.json").unlink(missing_ok=True)
    PR.save(rd, [PR.code(PR.MUST, "1 accuracy", "the protrusion measures 3 mm on the page, not as written")])
    md = (rd / "final-chronology.md").read_text(encoding="utf-8")
    old = next(ln for ln in md.split("\n") if "3 mm" in ln)
    return job_dir, sd, old, old.replace("disc protrusion", "posterior disc protrusion")


def test_a_corrected_copy_rehearses_the_final_set_and_every_stage_to_the_paid_review(
    scene, data_root, firm_config_path, pricing_path, monkeypatch, tmp_path
) -> None:
    from jobs.medchron import rehearsal
    from test_update_decide_rehearse import _Forbidden, _no_seat, _snapshot

    job_dir, sd, old, new = _held_at_review(scene, data_root, firm_config_path, pricing_path, monkeypatch)
    live = _snapshot(job_dir), _snapshot(sd)
    copy = rehearsal.prepare(job_dir)
    staged = tmp_path / "decisions.json"
    staged.write_text(json.dumps({"corrections": [{"old": old, "new": new}]}))
    argv = ["01JOB", "--decisions", str(staged), "--by", "scott@smd.services", "--source", "vfy_x", "--reason", "r"]
    assert UD.main([*argv, "--offline", str(copy)]) == 0 and staged.exists()
    model = _Forbidden()
    kw = {"firm_config": str(firm_config_path), "pricing": str(pricing_path), "log": lambda *_: None}
    work, outs = rehearsal.run(copy, seat_factory=_no_seat, client=model, **kw)
    notes = "\n".join(outs[0].notes)
    assert model.calls == []
    for name in UDR.REDO[:-1]:
        assert f"rehearse {name}: ok" in notes, notes
    assert outs[0].outcome == "rehearsed" and outs[0].stage == "review" and "rehearse review: STOP paid" in notes
    cs = work / "data" / "example-matter" / "runs" / "alpha"
    upd = (cs / "update" / "updated-chronology.md").read_text(encoding="utf-8")
    assert new in upd and old not in upd  # the final set and assemble carried the corrected sentence
    mark = json.loads((cs / "update" / FS.REPORT).read_text(encoding="utf-8"))
    pre = (cs / "final-chronology.pre-final-set.md").read_text(encoding="utf-8")
    assert new in pre and mark["in_sha"] == UD._sha(pre)  # the final set ran afresh on the corrected document
    assert mark["counts"]["new claim keys created"] == 0 and mark["counts"]["claim keys lost unaccounted"] == 0
    proofs = json.loads((cs / "update" / "proofs.json").read_text(encoding="utf-8"))
    assert proofs["fails"] == []
    assert (_snapshot(job_dir), _snapshot(sd)) == live


def test_falsifier_the_update_review_verb_alone_still_refuses_a_review_hold(tmp_path, job_dir, data_root) -> None:
    d, _sd, _paths = _review_seat(tmp_path, job_dir, data_root)
    with pytest.raises(UD.Refused, match="not held by the update's"):
        UD.plan(d, T.JOB, corrections=[{"old": T.WRONG, "new": T.RIGHT}], by="a", source="b", reason="c")
