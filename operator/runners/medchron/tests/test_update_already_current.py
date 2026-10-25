"""An update into the delivered chronology that finds nothing new is an
ANSWER, not a hold (2026-10-25, ``update/outcome.py``).

A firm asked for an update naming the files its delivered chronology did not
cite; the delivery had read every one. The lane held on that likely answer
(nothing new to read, nothing new to add, every named file excluded by the
firm's rules), and a held chronology tells the firm nothing. Now each ends
DELIVERED already current with nothing filed, a coded per-file record, a
zero-file proof the daemon's gate accepts only on that record, and a wake that
carries the counts. Each case has its falsifier. Every name is synthetic.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from medchron import dag, dag_update, unit_proof
from medchron.proof import gate, holds
from medchron.stages import download as download_stage, update_collect as UC
from medchron.stages.review import docx_text
from medchron.update import outcome as UO, review_list
from medchron.update.coverage import covered_sets
from test_update_e2e import _driver, _job, _Model, _seat
import update_testkit as K

PHOTOS_WHY = "outside the firm's folder rules (an excluded folder or kind)"
#: The records review an already-current update files (the driver stamps today's date).
REVIEW = review_list.name("Alpha Example", time.strftime("%m-%d-%y"))


def _run(tmp_path: Path, data_root: Path, firm: Path, pricing: Path, ids: list[str]) -> tuple[Any, Any, _Model]:
    job_dir = _job(tmp_path, data_root, ids)

    seat, model = _seat(tmp_path, K.base_md()), _Model()
    return _driver(job_dir, firm, pricing, seat, model).run()[0], seat, model


def _record(data_root: Path) -> dict[str, Any]:
    return json.loads((data_root / "example-matter" / "runs" / "alpha" / "update" / UO.RECORD).read_text())


def _state(data_root: Path) -> dict[str, Any]:
    return json.loads((data_root / "example-matter" / "runs" / "alpha" / "state.json").read_text())


# ---- case 1: nothing new to read (update_select) -------------------------------------
def test_an_update_whose_files_the_delivery_already_holds_is_delivered_already_current(
    tmp_path: Path, data_root: Path, firm_config_path: Path, pricing_path: Path
) -> None:
    """n2 is a byte-identical copy of a delivered exhibit, n3 a photo the
    firm's folder rules exclude. Before: held at update_select, "nothing new
    to read". Now: delivered, nothing deleted, every file's reason recorded,
    the records review (one row per file) the one file filed beside the
    chronology and proven by hash, and the proof the gate believes."""
    o, seat, model = _run(tmp_path, data_root, firm_config_path, pricing_path, ["n2", "n3"])
    assert o.outcome == "delivered" and o.stage == "update_select", o
    assert [f["name"] for f in seat.sent] == [REVIEW] and seat.deleted == [] and model.calls == []
    rec = _record(data_root)
    reasons = {f["id"]: f["reason"] for f in rec["files"]}
    assert reasons == {"n2": UO.DUPLICATE, "n3": UO.EXCLUDED}
    assert next(f["text"] for f in rec["files"] if f["id"] == "n3") == f"excluded by the firm's rule: {PHOTOS_WHY}"
    assert rec["entries_added"] == 0 and rec["exhibits_added"] == 0 and UO.problem(rec) is None
    assert o.current == UO.summary(rec) and o.current["by_reason"][UO.DUPLICATE]["files"] == 1
    # The records review is the one file proven, and the daemon's gate believes it.
    assert o.proof["readback"] == "confirmed" and [f["name"] for f in o.proof["files"]] == [REVIEW]
    assert o.proof["review"] == "code_only" and o.current["review_file"] == REVIEW
    assert gate.check([o.to_dict()]) is None
    text = docx_text(data_root / "example-matter" / "out" / "alpha" / REVIEW)
    assert "a byte-identical copy of an exhibit the delivered chronology already holds" in text
    assert f"excluded by the firm's rule: {PHOTOS_WHY}" in text and "2 added nothing" in text
    # The covered record still names both, so the next update does not read them again.
    assert set(o.covered["covered"]) == {"n2", "n3"}
    st = _state(data_root)
    assert st["outcome"] == "delivered" and st["stages"]["update_select"]["status"] == "current"
    assert "map" not in st["stages"] or st["stages"]["map"]["status"] != "done"
    # The wake carries the counts, code-authored, never a file name.
    lines = UO.wake_lines([o.to_dict()], "delivered")
    assert lines[0].startswith("Update outcome: already_current.")
    assert "duplicate 1 file(s)" in lines[1] and "excluded_by_rule 1 file(s)" in lines[1]
    assert not any("photo" in ln for ln in lines)
    assert UO.wake_lines([o.to_dict()], "failed") == []


def test_the_daemon_wake_for_an_already_current_delivery_carries_the_counts(tmp_path: Path) -> None:
    from medchron import daemon as daemon_mod, verdict

    d = daemon_mod.Daemon.__new__(daemon_mod.Daemon)
    jd = tmp_path / "jobs" / "J1"
    jd.mkdir(parents=True)
    (jd / "envelope.json").write_text(json.dumps({"matter": {"number": "2099-EX-0001"}, "update": {"into": "x"}}))
    s = {"kind": UO.KIND, "requested": 2, "files": 2, "entries_added": 0, "exhibits_added": 0}
    s["by_reason"] = {"billing_only": {"files": 1, "pages": 3}, "read_no_new_visit": {"files": 1, "pages": 9}}
    verdict.write(jd, json.dumps([{"unit": "alpha", "outcome": "delivered", "current": s}]))
    d.job_dir = lambda _j: jd  # type: ignore[method-assign]
    text = d._compose_wake("J1", "delivered", {})
    assert "Update outcome: already_current. The delivered chronology is unchanged." in text
    assert "files asked: 2; files accounted for: 2; by kind: billing_only 1 file(s) 3 page(s)" in text
    assert "Delivered folder id" not in text
    assert "Update outcome" not in d._compose_wake("J1", "failed", {}, stage="update_select")


# ---- case 4: every named file excluded (download) -----------------------------------
def test_every_named_file_excluded_by_the_firms_rules_is_delivered_already_current(
    tmp_path: Path, data_root: Path, firm_config_path: Path, pricing_path: Path
) -> None:
    """Before: held at download with "a named document id is not on the
    matter", which was untrue. Now delivered, nothing pulled, the records
    review the one file filed, and the selection records the exclusion so
    coverage counts it."""
    o, seat, _ = _run(tmp_path, data_root, firm_config_path, pricing_path, ["n3"])
    assert o.outcome == "delivered" and o.stage == "download", o
    assert [f["name"] for f in seat.sent] == [REVIEW] and seat.deleted == []
    assert not any("n3" in m for m in seat.mints)
    rec = _record(data_root)
    assert [(f["id"], f["reason"]) for f in rec["files"]] == [("n3", UO.EXCLUDED)]
    assert gate.check([o.to_dict()]) is None
    sel = json.loads((data_root / "example-matter" / "runs" / "alpha" / "update" / "selection.json").read_text())
    assert [x["id"] for x in sel["excluded_named"]] == ["n3"]
    assert o.covered["covered"] == ["n3"] and o.covered["uncovered"] == []


def test_a_named_id_the_matter_does_not_carry_still_holds_with_its_true_reason(
    tmp_path: Path, data_root: Path, firm_config_path: Path, pricing_path: Path
) -> None:
    o, seat, _ = _run(tmp_path, data_root, firm_config_path, pricing_path, ["n3", "zz-not-there"])
    assert o.outcome == "held" and o.stage == "download", o
    assert "not on the matter" in (o.reason or "") and seat.sent == []
    assert o.current is None and o.proof is None


def test_the_all_excluded_exit_is_current_on_an_update_and_an_honest_hold_on_an_append_run() -> None:
    upd = next(s for s in dag_update.UPDATE_STAGES if s.name == "download")
    assert upd.exit_map[download_stage.EXIT_ALL_EXCLUDED][0] == dag.CURRENT
    run = dag.BY_NAME["download"].exit_map[download_stage.EXIT_ALL_EXCLUDED]
    assert run[0] == dag.HELD and "excluded by the firm's rules" in run[1] and "not on the matter" not in run[1]
    assert dag.BY_NAME["download"].exit_map[download_stage.EXIT_NAMED][0] == dag.HELD
    for name in ("update_select", "update_collect"):
        stage = next(s for s in dag_update.UPDATE_STAGES if s.name == name)
        assert stage.exit_map[UO.EXIT][0] == dag.CURRENT and stage.exit_map[2][0] == dag.HELD


# ---- case 2: nothing new to add (update_collect) -------------------------------------
def _collect_scene(tmp_path: Path) -> SimpleNamespace:
    sd = tmp_path / "matter"
    ud = sd / "runs" / "alpha" / "update"
    ud.mkdir(parents=True)
    (sd / "out" / "alpha").mkdir(parents=True)
    (ud / "base.md").write_text(K.base_md())
    rec = {"exhibit": 2, "title": "Exhibit 2 - Example Imaging (Medical Records)", "pages": 3, "read": [1, 2]}
    rec |= {"unread": [], "excluded": {"no text on the page": 1}, "id": "n1", "source_name": "Imaging.pdf"}
    bill = {"exhibit": 3, "title": "Exhibit 3 - Bills", "pages": 2, "read": [], "unread": []}
    bill |= {"excluded": {"billing page": 2}, "id": "n4", "source_name": "Bills.pdf"}
    (ud / "new_exhibits.json").write_text(json.dumps([rec, bill]))
    (ud / "selection.json").write_text(json.dumps({"exhibits": [rec, bill], "skipped": {}, "subsets": []}))
    (ud / "new_entries.md").write_text(
        "09/01/2026\nStale Clinic | Medical Diagnoses\n\nA stale entry. (Exhibit 2 - p. 1)"
    )
    (sd / "include.json").write_text(json.dumps({"include_file_ids": ["n1", "n4"]}))
    rows = [{"id": "n1", "name": "Imaging.pdf", "ext": ".pdf", "ok": True}]
    rows.append({"id": "n4", "name": "Bills.pdf", "ext": ".pdf", "ok": True})
    (sd / "raw_manifest.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    logged: list[str] = []
    return SimpleNamespace(slug_dir=sd, unit=SimpleNamespace(unit="alpha"), log=logged.append, logged=logged)


def test_composed_entries_the_delivery_already_lists_end_already_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sr = _collect_scene(tmp_path)
    monkeypatch.setattr(UC, "_shape", lambda *_a: (["dup"], {"kept": 0, "dropped_as_delivered": 1}))
    monkeypatch.setattr(UC.C, "compact", lambda e, p: ([], [], [], {}))
    assert UC.run_collect(sr) == UO.EXIT  # type: ignore[arg-type]
    assert not any(m.startswith("update_held:") for m in sr.logged)
    ud = sr.slug_dir / "runs" / "alpha" / "update"
    # This run's result is written as what it is: the stale entry cannot read as added.
    assert (ud / "new_entries.md").read_text() == "" and json.loads((ud / "new_exhibits.final.json").read_text()) == []
    rec = UO.write(sr.slug_dir, "alpha", "update_collect")
    assert {f["id"]: f["reason"] for f in rec["files"]} == {"n1": UO.NO_NEW_VISIT, "n4": UO.BILLING_ONLY}
    assert rec["by_reason"][UO.NO_NEW_VISIT] == {"files": 1, "pages": 3} and UO.problem(rec) is None


def test_falsifier_a_stale_added_entry_is_never_read_as_nothing_new(tmp_path: Path) -> None:
    sr = _collect_scene(tmp_path)  # new_entries.md still holds an earlier attempt's entry
    rec = UO.write(sr.slug_dir, "alpha", "update_collect")
    assert rec["entries_added"] == 1 and "were added" in (UO.problem(rec) or "")
    assert UO.proof(sr.slug_dir / "runs" / "alpha", [])["readback"] == "unproven"


def test_falsifier_a_document_selected_to_read_never_proves_nothing_new_at_select(tmp_path: Path) -> None:
    sr = _collect_scene(tmp_path)  # n1 has read pages: at update_select that is not nothing new
    rec = UO.write(sr.slug_dir, "alpha", "update_select")
    assert {f["id"]: f["reason"] for f in rec["files"]}["n1"] == UO.SELECTED
    assert "selected to read" in (UO.problem(rec) or "")


def test_falsifier_an_unaccounted_document_holds_as_ours_and_pages(tmp_path: Path) -> None:
    sr = _collect_scene(tmp_path)
    (sr.slug_dir / "include.json").write_text(json.dumps({"include_file_ids": ["n1", "n4", "n9"]}))
    (sr.slug_dir / "runs" / "alpha" / "update" / "new_entries.md").write_text("")
    (sr.slug_dir / "runs" / "alpha" / "update" / "new_exhibits.final.json").write_text("[]")
    calls: list[tuple] = []
    st = SimpleNamespace(finish=lambda *a, **k: calls.append(("finish", a, k)), end=lambda *a: calls.append(("end", a)))
    out = SimpleNamespace(outcome="delivered", reason=None, dollars=0.0, pages=0, current=None)
    got = UO.end(sr, st, "update_collect", UO.EXIT, out)
    assert got.outcome == "held" and got.current is None
    assert got.reason.startswith("update_held: the update found nothing to add, and 1 document(s) are not accounted")
    assert ("end", ("held", got.reason)) in calls
    # Ours, paged under update_held; never the legacy already_current class.
    assert holds.classify("medchron", got.reason) == (holds.SMD_FINISHING, "update_held")


# ---- the proof gate: the zero-file kind only on a record that proves it --------------
def _proof(**cur: Any) -> dict[str, Any]:
    s = {"kind": "already_current", "requested": 2, "files": 2, "entries_added": 0, "exhibits_added": 0}
    s["by_reason"] = {"billing_only": {"files": 1, "pages": 2}, "duplicate": {"files": 1, "pages": 0}}
    s |= cur
    return {
        "readback": "nothing_filed",
        "files": [],
        "nothing_to_file": True,
        "review": "code_only",
        "already_current": s,
    }


def _delivered(proof: dict[str, Any] | None) -> dict[str, Any]:
    return {"unit": "alpha", "outcome": "delivered", "proof": proof}


def test_the_gate_accepts_the_zero_file_kind_only_with_a_coherent_record() -> None:
    assert gate.check([_delivered(_proof())]) is None
    assert gate.check([_delivered(_proof(entries_added=3))])[0] == "readback_unproven"
    assert gate.check([_delivered(_proof(exhibits_added=1))])[0] == "readback_unproven"
    assert gate.check([_delivered(_proof(files=3))])[0] == "readback_unproven"  # counts do not add up
    assert gate.check([_delivered(_proof(requested=5))])[0] == "readback_unproven"  # an asked file unaccounted
    bad = _proof(by_reason={"unaccounted": {"files": 2, "pages": 0}})
    assert gate.check([_delivered(bad)])[0] == "readback_unproven"
    filed = _proof()
    filed["files"] = [{"name": "x.docx", "file_id": "f1", "size": 1, "sha256": "a" * 64}]
    assert gate.check([_delivered(filed)])[0] == "readback_unproven"
    # The records review filed and proven: believed. Anything beside it, or unproven: not.
    ok = _proof(review_file=REVIEW)
    ok |= {"readback": "confirmed", "files": [{"name": REVIEW, "file_id": "f1", "size": 1, "sha256": "a" * 64}]}
    assert gate.check([_delivered(ok)]) is None
    extra = dict(ok, files=ok["files"] + [{"name": "Exhibit 9.pdf", "file_id": "f2", "size": 1, "sha256": "b" * 64}])
    assert gate.check([_delivered(extra)])[0] == "readback_unproven"
    assert gate.check([_delivered(dict(ok, readback="unproven"))])[0] == "readback_unproven"
    assert gate.check([_delivered(_proof(review_file=REVIEW))])[0] == "readback_unproven"  # claimed, not filed
    no_record = {"readback": "nothing_filed", "files": [], "review": "code_only"}
    assert gate.check([_delivered(no_record)])[0] == "readback_unproven"


def test_falsifier_an_update_that_added_entries_and_filed_nothing_is_never_delivered(tmp_path: Path) -> None:
    """A delivered update outcome with no file read back and no outcome
    record (the normal path, its upload lost) gets the ordinary block, which
    says unproven; and an outcome claiming already current whose record shows
    entries added gets the unproven zero-file block. Neither passes."""
    rd = tmp_path / "runs" / "alpha"
    (rd / "update").mkdir(parents=True)
    plain = SimpleNamespace(outcome="delivered", files=[], current=None)
    assert gate.check([_delivered(unit_proof.for_outcome(plain, rd, None))])[0] == "readback_unproven"
    rec = {"kind": UO.KIND, "requested": 1, "files": [], "by_reason": {}, "entries_added": 2, "exhibits_added": 1}
    (rd / "update" / UO.RECORD).write_text(json.dumps(rec))
    claimed = SimpleNamespace(outcome="delivered", files=[], current={"kind": UO.KIND})
    proof = unit_proof.for_outcome(claimed, rd, None)
    assert proof["readback"] == "unproven" and gate.check([_delivered(proof)])[0] == "readback_unproven"


def test_a_photograph_the_transcription_could_not_read_is_uncovered_and_named(tmp_path: Path) -> None:
    sd = tmp_path / "matter"
    ud = sd / "runs" / "alpha" / "update"
    ud.mkdir(parents=True)
    (ud / "selection.json").write_text(json.dumps({"exhibits": [], "skipped": {UO.IMAGE_UNREAD: ["p1"]}}))
    (sd / "raw_manifest.jsonl").write_text(json.dumps({"id": "p1", "name": "scene.jpg", "ext": ".jpg", "ok": True}))
    (sd / "include.json").write_text(json.dumps({"include_file_ids": ["p1"]}))
    assert covered_sets(sd, "alpha")["uncovered"] == ["p1"]
    rec = UO.write(sd, "alpha", "update_select")
    assert rec["files"][0]["reason"] == UO.UNREADABLE and "next update reads it again" in rec["files"][0]["text"]
    assert UO.problem(rec) is None


def test_a_mixed_update_names_the_requested_files_the_firms_rules_left_out_with_the_rule(tmp_path: Path) -> None:
    """Case 2, mixed: one file adds a visit, two the firm's rules kept out.
    Records Reviewed named every other file with what was done with it, but
    a requested file the rules excluded was named nowhere (2026-10-25)."""
    from medchron.stages.update_finish import update_note

    sd = tmp_path / "matter"
    ud = sd / "runs" / "alpha" / "update"
    ud.mkdir(parents=True)
    keep = [{"exhibit": 2, "title": "Exhibit 2 - Imaging", "pages": 2, "read": [1, 2], "excluded": {}, "id": "n1"}]
    excluded = [
        {"id": "n3", "name": "scene photo.jpg", "why": PHOTOS_WHY},
        {"id": "n5", "name": "Letter of representation.pdf", "why": "excluded by the firm's file-name rule"},
    ]
    (ud / "selection.json").write_text(json.dumps({"exhibits": keep, "skipped": {}, "excluded_named": excluded}))
    (ud / "new_exhibits.final.json").write_text(json.dumps(keep))
    (sd / "raw_manifest.jsonl").write_text(json.dumps({"id": "n1", "name": "Imaging.pdf", "ext": ".pdf", "ok": True}))
    text = update_note(SimpleNamespace(job=None), ud)({2: "Example Imaging - 11-03-2025"})  # type: ignore[arg-type]
    assert "1 medical record document(s) filed" in text
    assert f"1 requested document was not read ({PHOTOS_WHY}): scene photo.jpg." in text
    assert (
        "1 requested document was not read (excluded by the firm's file-name rule): Letter of representation.pdf."
        in text
    )


def test_every_disclosed_reason_has_fixed_wording_in_the_skill() -> None:
    skill = (Path(__file__).resolve().parents[3] / "skills" / "medical-chronology-maintainer" / "SKILL.md").read_text()
    flat = " ".join(skill.split())
    assert "`Update outcome: already_current.`" in flat
    for code in set(UO.WORDING) - UO.NOT_PROVEN:
        assert f"`{code}`" in flat, code
