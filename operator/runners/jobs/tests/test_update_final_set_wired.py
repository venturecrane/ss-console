"""The final set WIRED into the update job (2026-11-17): the real driver walks
the real update DAG, and `update_final_set` runs between `update_review` and
`update_assemble`.

Each test drives the update e2e scene (``test_update_e2e``: invented records,
a fake seat, a fake model) through ``medchron/driver.Driver``. Where a test
needs a stray citation-only line, it wraps the REAL `update_review` runner and
adds one line to what that stage wrote; every stage still runs its own code.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest

from jobs.medchron import dag, dag_update, driver as driver_mod
from jobs.medchron.state import RunState, state_path
from jobs.medchron.update import docx_md, final_set as FS, final_set_io as FIO, proofs_final as PF, uncited as U
from jobs.stages import update_final_set, update_finish, update_review
from test_update_e2e import _driver, _Model, _upload_pause, scene  # noqa: F401 - `scene` is a fixture

#: The MRI claim the e2e scene composes; the stray line repeats its citation
#: on a line of its own, the shape the final set removes (64 to 0 on a real update).
MRI = "MRI showed a disc protrusion measuring 3 mm at the fifth and sixth level. (Exhibit 2 - p. 2)"
STRAY = "(Exhibit 2 - p. 2)"


def _doc(sd: Path) -> Path:
    return sd / "runs" / "alpha" / "final-chronology.md"


def _with_stray(order: list[str]):
    """The real update_review, then one citation-only line under the MRI claim."""

    def run(sr):
        rc = update_review.run(sr)
        order.append("update_review")
        p = _doc(sr.slug_dir)
        md = p.read_text(encoding="utf-8")
        assert MRI in md, "the e2e scene's MRI claim is the line the stray citation sits under"
        p.write_text(md.replace(MRI, f"{MRI}\n{STRAY}", 1), encoding="utf-8")
        return rc

    return run


def _traced(name: str, real, order: list[str]):
    def run(sr):
        order.append(name)
        return real(sr)

    return run


def _filed_lines(seat, o, tmp_path: Path) -> list[str]:
    new_id = next(f["file_id"] for f in o.proof["files"] if f["name"].startswith("Alpha Example - Medical Chronology"))
    got = tmp_path / "filed.docx"
    got.write_bytes(seat.blobs[new_id])
    return docx_md.summary_lines(got)


def test_the_wired_stage_sits_between_review_and_assemble_and_is_rehearsable() -> None:
    seq = dag.stages(update=True)
    names = [s.name for s in seq]
    i = names.index("update_final_set")
    assert names[i - 1] == "update_review" and names[i + 1] == "update_assemble"
    by = {s.name: s for s in seq}
    fin = by["update_final_set"]
    assert fin.requires == ("update_review",) and by["update_assemble"].requires == ("update_final_set",)
    assert fin.runner is update_final_set.run and fin.exit_map == update_final_set.EXITS
    assert not fin.paid and not fin.external  # `jobs rehearse --redo update_final_set` is permitted
    # A redo of the review reopens the final set and what reads its document; the final set
    # reopens nothing, so its no-op on an update that already sent rebuilds nothing.
    assert {"update_final_set", "update_assemble", "update_render", "update_proofs", "update_manifest"} <= set(
        by["update_review"].invalidates
    )
    assert fin.invalidates == ()
    assert dag._validate(seq) == []
    # The fresh chronology's order is unchanged: proof 15 has no stage in a full run.
    assert "final_proof" not in [s.name for s in dag.STAGES]


def test_an_update_runs_the_final_set_and_files_no_stray_line(
    scene, data_root: Path, firm_config_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    job_dir, seat = scene
    _upload_pause(monkeypatch)
    order: list[str] = []
    assemble_joins: list[str] = []
    ran_at_assemble: list[bool] = []
    real_join = U.join_stray

    def assemble(sr):
        ran_at_assemble.append(FIO.ran(sr.slug_dir / "runs" / sr.unit.unit / "update"))
        order.append("update_assemble")
        monkeypatch.setattr(
            U, "join_stray", lambda e, h=None, audited=False: assemble_joins.append(e) or real_join(e, h, audited)
        )
        try:
            return update_finish.run_assemble(sr)
        finally:
            monkeypatch.setattr(U, "join_stray", real_join)

    d = _driver(job_dir, firm_config_path, pricing_path, seat, _Model())
    d._runner_override = {
        "update_review": _with_stray(order),
        "update_final_set": _traced("update_final_set", update_final_set.run, order),
        "update_assemble": assemble,
    }
    o = d.run()[0]
    sd = data_root / "example-matter"
    assert o.outcome == "delivered" and o.stage is None, o
    assert order == ["update_review", "update_final_set", "update_assemble"]
    # The final set ran, so assemble did not join stray lines a second time.
    assert ran_at_assemble == [True] and assemble_joins == []
    mark = json.loads((sd / "runs" / "alpha" / "update" / FS.REPORT).read_text())
    assert mark["counts"]["stray lines before"] >= 1 and mark["counts"]["stray lines after"] == 0
    assert STRAY not in _doc(sd).read_text().split("\n")
    # The filed document carries no line that is only a citation.
    assert not [ln for ln in _filed_lines(seat, o, tmp_path) if U.cite_only(ln)]
    proofs = json.loads((sd / "runs" / "alpha" / "update" / "proofs.json").read_text())
    assert proofs["fails"] == []
    assert "11. ok" in proofs["lines"]
    for n in ("12", "13", "14", "15"):
        assert any(ln.startswith(f"{n}. (recorded, never fails the run)") for ln in proofs["lines"]), n
    assert proofs["filed"]["sha256"] and set(proofs["recorded"]) >= {"P15_unsupported_claim", "P15_ungraded_text"}


def _failing(code: str):
    return lambda _x: Counter({code: 1})


def test_failing_record_only_proofs_are_recorded_and_the_update_still_delivers(
    scene, data_root: Path, firm_config_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: proofs 12, 13, 14 and 15 all find something. Each is recorded;
    none fails the run, and the update is filed."""
    job_dir, seat = scene
    _upload_pause(monkeypatch)
    for n in ("12", "13", "14"):
        monkeypatch.setitem(PF.CHECKS, n, _failing(f"P{n}_mutated"))
    real_codes = PF.GP.codes
    monkeypatch.setattr(PF.GP, "codes", lambda res: {**real_codes(res), "P15_unsupported_claim": 1})
    o = _driver(job_dir, firm_config_path, pricing_path, seat, _Model()).run()[0]
    assert o.outcome == "delivered", o
    proofs = json.loads((data_root / "example-matter" / "runs" / "alpha" / "update" / "proofs.json").read_text())
    assert proofs["fails"] == []
    assert {
        k: proofs["recorded"][k] for k in ("P12_mutated", "P13_mutated", "P14_mutated", "P15_unsupported_claim")
    } == {
        "P12_mutated": 1,
        "P13_mutated": 1,
        "P14_mutated": 1,
        "P15_unsupported_claim": 1,
    }
    assert seat.sent


def test_only_proof_11_of_the_final_proofs_can_fail_an_update(
    scene, data_root: Path, firm_config_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every one of proofs 11 to 15 finds something: the update fails on 11
    alone, and nothing is filed."""
    job_dir, seat = scene
    _upload_pause(monkeypatch)
    assert PF.ENFORCE == frozenset({"11"}) and PF.RECORD == frozenset({"12", "13", "14", "15"})
    for n in ("11", "12", "13", "14"):
        monkeypatch.setitem(PF.CHECKS, n, _failing(f"P{n}_mutated"))
    real_codes = PF.GP.codes
    monkeypatch.setattr(PF.GP, "codes", lambda res: {**real_codes(res), "P15_ungraded_text": 1})
    o = _driver(job_dir, firm_config_path, pricing_path, seat, _Model()).run()[0]
    assert o.outcome == "failed" and o.stage == "update_proofs", o
    proofs = json.loads((data_root / "example-matter" / "runs" / "alpha" / "update" / "proofs.json").read_text())
    assert proofs["fails"] == ["11"]
    assert seat.sent == [] and seat.deleted == []


@pytest.mark.parametrize(
    ("exc", "code"), [(FS.CreatedKey, update_final_set.EXIT_CREATED), (FS.LostKey, update_final_set.EXIT_LOST)]
)
def test_a_final_set_refusal_ends_the_update_refused_and_nothing_is_filed(
    scene,
    data_root: Path,
    firm_config_path: Path,
    pricing_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exc: type[AssertionError],
    code: int,
) -> None:
    job_dir, seat = scene
    _upload_pause(monkeypatch)

    def refuse(_x):
        raise exc("a claim key changed")

    monkeypatch.setattr(FS, "run", refuse)  # the stage's own call (`FS.run(x)`) raises its own refusal
    o = _driver(job_dir, firm_config_path, pricing_path, seat, _Model()).run()[0]
    assert o.outcome == "refused" and o.stage == "update_final_set", o
    assert o.reason and o.reason.startswith(update_final_set.EXITS[code][1]), o.reason
    assert seat.sent == [] and seat.deleted == []
    out = data_root / "example-matter" / "out" / "alpha"
    assert not list(out.glob("Alpha Example - Medical Chronology*")) if out.is_dir() else True


def test_redo_update_review_reruns_the_final_set_and_what_reads_its_document(
    scene, data_root: Path, firm_config_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_dir, seat = scene
    _upload_pause(monkeypatch)
    assert _driver(job_dir, firm_config_path, pricing_path, seat, _Model()).run()[0].outcome == "delivered"
    order: list[str] = []
    d = driver_mod.Driver(
        job_dir,
        firm_config=str(firm_config_path),
        pricing=str(pricing_path),
        log=lambda *_: None,
        seat_factory=lambda: seat,
        client=_Model(),
        redo=("update_review",),
    )
    d._runner_override = {
        n: _traced(n, r, order)
        for n, r in (
            ("update_review", update_review.run),
            ("update_final_set", update_final_set.run),
            ("update_assemble", update_finish.run_assemble),
            ("update_render", update_finish.run_render),
        )
    }
    d.run()
    assert order == ["update_review", "update_final_set", "update_assemble", "update_render"]


def test_a_crash_in_the_final_proofs_is_recorded_and_the_update_still_delivers(
    scene, data_root: Path, firm_config_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: proofs 11 to 15 raise. Proofs 1 to 10 decide the run as on
    main; the crash is recorded, never a stopped update."""
    job_dir, seat = scene
    _upload_pause(monkeypatch)

    def crash(_sr):
        raise ValueError("proof code crashed")

    monkeypatch.setattr(update_finish, "final_proofs", crash)
    o = _driver(job_dir, firm_config_path, pricing_path, seat, _Model()).run()[0]
    assert o.outcome == "delivered", o
    proofs = json.loads((data_root / "example-matter" / "runs" / "alpha" / "update" / "proofs.json").read_text())
    assert proofs["fails"] == [] and proofs["recorded"] == {update_finish.FINAL_PROOFS_ERROR: 1}
    assert not any(ln.startswith("11.") for ln in proofs["lines"])
    assert seat.sent


def _old_dag() -> tuple[dag.Stage, ...]:
    """The update's order before this change: no final set, assemble after the review."""
    out = []
    for s in dag_update.UPDATE_STAGES:
        if s.name == "update_final_set":
            continue
        if s.name == "update_review":
            s = replace(s, invalidates=())
        if s.name == "update_assemble":
            s = replace(s, requires=("update_review",))
        out.append(s)
    return tuple(out)


def _sent_then_resumed(job_dir: Path, data_root: Path, firm: Path, pricing: Path, seat, monkeypatch) -> list[str]:
    """An update run to its send on the old order, then its upload reopened (a
    requeue after a short read-back, or a hold at upload resumed)."""
    with monkeypatch.context() as m:
        m.setattr(dag_update, "UPDATE_STAGES", _old_dag())
        first = _driver(job_dir, firm, pricing, seat, _Model()).run()[0]
    assert first.outcome == "delivered" and seat.sent, first
    st = RunState.load_or_new(state_path(data_root, "example-matter", "alpha"), slug="example-matter", unit="alpha")
    assert "update_final_set" not in st.stages
    st.invalidate(["upload"])
    return [s["name"] for s in seat.sent]


def test_an_update_that_already_sent_its_files_resumes_with_nothing_rebuilt(
    scene, data_root: Path, firm_config_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_dir, seat = scene
    _upload_pause(monkeypatch)
    sent = _sent_then_resumed(job_dir, data_root, firm_config_path, pricing_path, seat, monkeypatch)
    sd = data_root / "example-matter"
    before = _doc(sd).read_bytes()
    order: list[str] = []
    d = _driver(job_dir, firm_config_path, pricing_path, seat, _Model())
    d._runner_override = {
        n: _traced(n, r, order)
        for n, r in (
            ("update_final_set", update_final_set.run),
            ("update_assemble", update_finish.run_assemble),
            ("update_render", update_finish.run_render),
        )
    }
    o = d.run()[0]
    assert o.outcome == "delivered", o
    assert order == ["update_final_set"]  # nothing rebuilt
    assert [s["name"] for s in seat.sent] == sent  # no second send
    assert _doc(sd).read_bytes() == before and not (sd / "runs" / "alpha" / "update" / FS.REPORT).exists()
    log = (sd / "runs" / "alpha" / "log-update_final_set.txt").read_text()
    assert "already sent: left as filed" in log


def test_falsifier_a_final_set_that_rebuilds_after_the_send_holds_the_update(
    scene, data_root: Path, firm_config_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: no guard, and the final set reopening assembly onward (this
    PR's first version). The resumed update rebuilds what it sent; on a real
    update the rebuilt chronology differs (exhibit titles weighted by cited
    pages, the final set's drops), modelled here by one changed title word at
    assembly. The filing then holds it for a person: the case the guard and
    the order close."""
    job_dir, seat = scene
    _upload_pause(monkeypatch)
    sent = _sent_then_resumed(job_dir, data_root, firm_config_path, pricing_path, seat, monkeypatch)
    reopen = ("update_assemble", "update_render", "update_proofs", "update_manifest")
    monkeypatch.setattr(
        dag_update,
        "UPDATE_STAGES",
        tuple(replace(s, invalidates=reopen) if s.name == "update_final_set" else s for s in dag_update.UPDATE_STAGES),
    )
    monkeypatch.setattr(update_final_set, "already_sent", lambda _sr: False)
    order: list[str] = []

    def assemble(sr):
        order.append("update_assemble")
        rc = update_finish.run_assemble(sr)
        p = sr.slug_dir / "runs" / sr.unit.unit / "update" / "updated-chronology.md"
        p.write_text(p.read_text().replace("Example Imaging", "Example Imaging Center", 1))
        return rc

    d = _driver(job_dir, firm_config_path, pricing_path, seat, _Model())
    d._runner_override = {"update_assemble": assemble}
    o = d.run()[0]
    assert order == ["update_assemble"]  # the send's files were rebuilt
    assert o.outcome == "held" and o.stage == "upload", o
    assert [s["name"] for s in seat.sent] == sent  # never a second copy
