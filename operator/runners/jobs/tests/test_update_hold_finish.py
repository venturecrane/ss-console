"""SMD finishes an update the entry read held (``stages/entry_audit_held.py``):
the held line is corrected (or the held entry deleted whole) in
``runs/<unit>/final-chronology.md`` and the job resumes. Proof 10 must then
name what SMD took out, and a resume with no edit holds again and pays for
nothing new. The update e2e scene through the real driver, DAG and stages;
the model flags the MRI sentence and its second reader confirms it wrong.
Every name, date and value is invented."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from jobs.medchron.update import body as B
from test_update_e2e import ALL_OK, _driver, _Model, _Stream, _text, _upload_pause, scene  # noqa: F401 - scene is a pytest fixture the tests request by name

MRI = "measuring 3 mm"


class _Flags(_Model):
    """The e2e model, but the entry read flags the MRI sentence while it says
    3 mm, and every second reader (verify or confirm) finds the line wrong
    and the proposed fix unsupported."""

    def __init__(self, finding: dict[str, Any]) -> None:
        super().__init__()
        self.finding = finding
        self.checks = 0

    def stream(self, **kw: Any) -> Any:
        system = self._system(kw)
        if not any(m in system for m in ("ENTRY READ:", "ENTRY VERIFY", "ENTRY CONFIRM")):
            return super().stream(**kw)
        text = "\n".join(b.get("text", "") for b in kw["messages"][0]["content"] if b.get("type") == "text")
        if "ENTRY READ:" in system and "] MRI showed a disc protrusion " + MRI in text:  # the line, not the page
            self.calls.append(kw)
            return _Stream(_text(json.dumps({"lines": ALL_OK, "findings": [self.finding], "complete": True})))
        if "ENTRY VERIFY" in system or "ENTRY CONFIRM" in system:
            self.calls.append(kw)
            self.checks += 1
            answer = {"original_wrong": True, "corrected_supported": False, "page": {"exhibit": 2, "page": 2}}
            return _Stream(_text(json.dumps({**answer, "reason": "the page says otherwise"})))
        return super().stream(**kw)


VALUE = {"line": "S1", "class": "value", "exhibit": 2, "page": 2, "quote": "3 mm", "action": "replace",
         "replacement": "4 mm", "problem": "the page records 4 mm"}  # fmt: skip
OTHER = {"line": "S1", "class": "another_person", "exhibit": 2, "page": 2, "quote": "3 mm", "action": "none",
         "replacement": "", "problem": "this study is another patient's"}  # fmt: skip


def _held(scene: Any, data_root: Path, firm: Path, pricing: Path, finding: dict[str, Any]) -> tuple[Any, Path]:
    job_dir, seat = scene
    o = _driver(job_dir, firm, pricing, seat, _Flags(finding)).run()[0]
    assert o.outcome == "held" and o.stage == "audit" and not seat.sent, o
    return scene, data_root / "example-matter" / "runs" / "alpha" / "final-chronology.md"


def _resume(scene: Any, firm: Path, pricing: Path, finding: dict[str, Any]) -> tuple[Any, _Flags]:
    job_dir, seat = scene
    model = _Flags(finding)
    return _driver(job_dir, firm, pricing, seat, model).run()[0], model


def test_smd_corrects_the_held_value_and_the_resumed_update_files(
    scene, data_root: Path, firm_config_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _upload_pause(monkeypatch)
    sc, doc = _held(scene, data_root, firm_config_path, pricing_path, VALUE)
    doc.write_text(doc.read_text(encoding="utf-8").replace("3 mm", "4 mm"), encoding="utf-8")  # SMD's correction
    o, _m = _resume(sc, firm_config_path, pricing_path, VALUE)
    assert o.outcome == "delivered", o
    ud = data_root / "example-matter" / "runs" / "alpha" / "update"
    proofs = json.loads((ud / "proofs.json").read_text())
    assert proofs["fails"] == [] and any(ln.startswith("10.") for ln in proofs["lines"])
    assert "4 mm" in (ud / "updated-chronology.md").read_text() and seat_sent(sc)


def test_smd_deletes_an_another_persons_entry_whole_and_the_resumed_update_files(
    scene, data_root: Path, firm_config_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _upload_pause(monkeypatch)
    sc, doc = _held(scene, data_root, firm_config_path, pricing_path, OTHER)
    md = doc.read_text(encoding="utf-8")
    (mri,) = [e for e in B.entries_of(md)[1] if MRI in e]
    doc.write_text(md.replace(mri + "\n\n", ""), encoding="utf-8")  # SMD's finish: the entry goes whole
    o, _m = _resume(sc, firm_config_path, pricing_path, OTHER)
    assert o.outcome == "delivered", o
    ud = data_root / "example-matter" / "runs" / "alpha" / "update"
    assert json.loads((ud / "proofs.json").read_text())["fails"] == []
    assert MRI not in (ud / "updated-chronology.md").read_text()


def test_a_resume_with_no_edit_holds_again_and_pays_for_nothing_new(
    scene, data_root: Path, firm_config_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _upload_pause(monkeypatch)
    sc, doc = _held(scene, data_root, firm_config_path, pricing_path, VALUE)
    before = doc.read_bytes()
    o, model = _resume(sc, firm_config_path, pricing_path, VALUE)
    assert o.outcome == "held" and o.stage == "audit", o
    assert model.checks == 0 and not [k for k in model.calls if "ENTRY" in model._system(k)]  # all on file
    assert doc.read_bytes() == before and not sc[1].sent


def test_falsifier_without_the_finish_named_proof_10_fails_the_corrected_update(
    scene, data_root: Path, firm_config_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: SMD's finish names nothing (``entry_audit_held.consume``
    returns before it compares). The corrected update then fails proof 10."""
    from jobs.stages import entry_audit_held as HELD

    _upload_pause(monkeypatch)
    sc, doc = _held(scene, data_root, firm_config_path, pricing_path, VALUE)
    doc.write_text(doc.read_text(encoding="utf-8").replace("3 mm", "4 mm"), encoding="utf-8")
    monkeypatch.setattr(HELD, "consume", lambda *_a: set())
    o, _m = _resume(sc, firm_config_path, pricing_path, VALUE)
    assert o.outcome == "failed" and o.stage == "update_proofs", o


def seat_sent(sc: Any) -> bool:
    return bool(sc[1].sent)
