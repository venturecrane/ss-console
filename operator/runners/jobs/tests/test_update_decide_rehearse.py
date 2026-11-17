"""SMD decides a held update on a rehearsal COPY, and ``jobs rehearse`` walks
what follows at $0 (2026-11-16).

WHY. An update held at its independent review on findings the reviewer got
wrong; SMD closes them with ``update_decide``. Before the live act, the ops
workspace rehearses every remaining stage on a copy: the decision applied to
the copy offline (no broker, daemon or queue), then ``jobs rehearse`` runs the
settled update review, assemble, render, the proofs, the manifest and the
pre-delivery review's code checks, and stops before any model call or upload.
Before this an update rehearsal raised KeyError at its first paid stage, and
no rehearsal could pass a paid stage. Every name and value is synthetic.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from jobs.medchron import rehearsal, update_decide as UD
from jobs.medchron.state import RunState
from jobs.stages import update_review_settle as URS
from test_update_e2e import _driver, _Model, _upload_pause, scene  # noqa: F401 - scene is a pytest fixture the tests below request by name

AFTER = ("update_review", "update_assemble", "update_render", "update_proofs", "update_manifest", "review", "upload")
CLOSE = {"anchor": "N1", "exhibit": 2, "page": 1, "composed_in": {"added": "N2"}, "why": "composed in the MRI entry"}


class _Forbidden:
    """A model client every call of which fails the test (and is counted)."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.messages = self

    def create(self, **_kw):
        self.calls.append("create")
        raise AssertionError("a rehearsal called the model")

    def stream(self, **_kw):
        self.calls.append("stream")
        raise AssertionError("a rehearsal called the model")


def _no_seat():
    raise AssertionError("a rehearsal opened the seat")


def _snapshot(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and not p.is_symlink() and "rehearsal" not in p.relative_to(root).parts
    }


def _held_job(scene, data_root, firm, pricing, monkeypatch) -> tuple[Path, Path]:
    """A delivered update turned back into one held at its independent review
    on one open finding, the document untouched: the state a decided hold
    resumes from."""
    job_dir, seat = scene
    _upload_pause(monkeypatch)
    assert _driver(job_dir, firm, pricing, seat, _Model()).run()[0].outcome == "delivered"
    sd = data_root / "example-matter"
    rd = sd / "runs" / "alpha"
    st = RunState.load_or_new(rd / "state.json", slug="example-matter", unit="alpha")
    for name in AFTER:
        st.stages[name].status = "pending"
    st.stages["update_review"].status = "held"
    st.outcome = st.outcome_reason = None
    st.save()
    md = (rd / "final-chronology.md").read_text(encoding="utf-8")
    f = {"severity": "MUST", "klass": "2 omitted content", "anchor": "N1", "exhibit": 2, "page": 1}
    f |= {"problem": "the lordosis is omitted", "action": "add", "corrected": "", "reason": "", "outcome": ""}
    rec = {"status": "held", "doc_sha_out": UD._sha(md), "open": [f], "rounds": [{"round": 1, "findings": [f]}]}
    (rd / "update" / UD.RECORD).write_text(json.dumps(rec), encoding="utf-8")
    return job_dir, sd


def _decide_offline(copy: Path, tmp_path: Path) -> list[str]:
    staged = tmp_path / "decisions.json"
    staged.write_text(json.dumps({"closures": [CLOSE]}))
    argv = ["01JOB", "--decisions", str(staged), "--by", "scott@smd.services", "--source", "vfy_x", "--reason", "r"]
    assert UD.main([*argv, "--offline", str(copy), "--dry-run"]) == 0
    assert UD.main([*argv, "--offline", str(copy)]) == 0
    assert staged.exists()  # kept for the live act
    return argv


def test_a_closed_hold_rehearses_every_stage_up_to_the_paid_review_and_spends_nothing(
    scene,
    data_root,
    firm_config_path,
    pricing_path,
    monkeypatch,
    tmp_path,
) -> None:
    job_dir, sd = _held_job(scene, data_root, firm_config_path, pricing_path, monkeypatch)
    live = _snapshot(job_dir), _snapshot(sd)
    copy = rehearsal.prepare(job_dir)
    _decide_offline(copy, tmp_path)
    copy_slug = copy / "data" / "example-matter"
    rec = json.loads((copy_slug / "runs" / "alpha" / "update" / UD.RECORD).read_text())
    assert rec["status"] == "clean" and rec["rounds"][-1]["findings"][0]["outcome"] == UD.CLOSED
    assert "the entry for 02/10/2026" in URS.notes(copy_slug / "out" / "alpha")[3]
    model = _Forbidden()
    kw = {"firm_config": str(firm_config_path), "pricing": str(pricing_path), "log": lambda *_: None}
    _w, outs = rehearsal.run(copy, seat_factory=_no_seat, client=model, **kw)
    notes = "\n".join(outs[0].notes)
    assert model.calls == []  # the model was never called
    assert "rehearse update_review: settled on this document" in notes
    for name in ("update_review", "update_assemble", "update_render", "update_proofs", "update_manifest"):
        assert f"rehearse {name}: ok" in notes, notes
    assert outs[0].outcome == "rehearsed" and outs[0].stage == "review"
    assert "rehearse review: STOP paid" in notes and "rehearse review: code checks pass" in notes
    assert "rehearse upload" not in notes
    # Nothing outside the copies was written: the live job and its data are byte-identical.
    assert (_snapshot(job_dir), _snapshot(sd)) == live


def test_an_unsettled_review_stops_the_rehearsal_and_is_never_run(
    scene,
    data_root,
    firm_config_path,
    pricing_path,
    monkeypatch,
) -> None:
    job_dir, _sd = _held_job(scene, data_root, firm_config_path, pricing_path, monkeypatch)
    copy = rehearsal.prepare(job_dir)  # still held: nothing decided on the copy
    model = _Forbidden()
    kw = {"firm_config": str(firm_config_path), "pricing": str(pricing_path), "log": lambda *_: None}
    _w, outs = rehearsal.run(copy, seat_factory=_no_seat, client=model, **kw)
    notes = "\n".join(outs[0].notes)
    assert outs[0].stage == "update_review" and "rehearse update_review: STOP paid" in notes
    assert "settled on this document" not in notes and model.calls == []


def test_a_settled_stage_that_reaches_for_the_model_fails_in_the_copy(
    scene,
    data_root,
    firm_config_path,
    pricing_path,
    monkeypatch,
    tmp_path,
) -> None:
    """The guard behind the rule: a rehearsal's model doorway raises, so even a
    stage let through as settled cannot spend."""
    job_dir, _sd = _held_job(scene, data_root, firm_config_path, pricing_path, monkeypatch)
    copy = rehearsal.prepare(job_dir)
    _decide_offline(copy, tmp_path)
    from jobs.stages import update_review as UR

    def reaches(sr):
        sr.client_factory()
        return 0

    monkeypatch.setattr(UR, "_reuse", lambda sr, rec, md: reaches(sr))
    model = _Forbidden()
    kw = {"firm_config": str(firm_config_path), "pricing": str(pricing_path), "log": lambda *_: None}
    _w, outs = rehearsal.run(copy, seat_factory=_no_seat, client=model, **kw)
    assert outs[0].stage == "update_review" and outs[0].outcome == "failed" and model.calls == []
    assert any("exit 1" in n for n in outs[0].notes)


@pytest.mark.parametrize("where", ["live", "outside"])
def test_offline_refuses_the_live_job_and_a_copy_whose_data_is_elsewhere(
    scene,
    data_root,
    firm_config_path,
    pricing_path,
    monkeypatch,
    tmp_path,
    where,
) -> None:
    job_dir, sd = _held_job(scene, data_root, firm_config_path, pricing_path, monkeypatch)
    target = job_dir
    if where == "outside":
        target = rehearsal.prepare(job_dir)
        body = (target / "job.yaml").read_text().replace(str(target / "data"), str(data_root))
        (target / "job.yaml").write_text(body)
    before = _snapshot(sd)
    with pytest.raises(UD.Refused, match="not a rehearsal copy|outside the copy"):
        UD.plan(
            None, "01JOB", corrections=[], closures=[UD._closure(0, CLOSE)], by="a", source="b", reason="c", copy=target
        )
    assert _snapshot(sd) == before
