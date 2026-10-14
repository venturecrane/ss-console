"""The update's review-and-repair loop (``stages/update_review.py``,
``update/review_repair.py``): a MUST-FIX the reviewer grounds on a page is
applied to the added entry, re-audited, re-reviewed, and the proofs run on the
corrected text; a MUST-FIX that never settles holds for SMD; a finding about a
delivered entry is never applied. Every value is invented."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import update_testkit as K
from medchron.proof import holds
from medchron.stages import update_review as UR
from medchron.update import body as B, review_repair as RR
from test_update_e2e import _driver, _Model, _Stream, _text, _upload_pause, scene  # noqa: F401 - scene is a pytest fixture the tests take by name

FIXED_N1 = "Cervical radiographs showed straightening of the lordosis with no fracture seen. (Exhibit 2 - p. 1)"


def _reply(*findings: dict) -> str:
    return json.dumps({"findings": list(findings), "complete": True})


def _must(anchor: str, corrected: str, *, page: int = 1, action: str = "replace") -> dict:
    return {
        "severity": "MUST",
        "class": "3 values",
        "anchor": anchor,
        "exhibit": 2,
        "page": page,
        "problem": "the time is the film's print time, not a finding",
        "action": action,
        "corrected": corrected,
        "reason": "read off the page",
    }


class _Reviewer(_Model):
    """The e2e model, with the update review and finisher scripted: review
    round r (r - 1 finisher calls so far) answers reviews[r - 1] (the last
    repeated), cut to each part's entries; the finisher answers in order."""

    def __init__(self, reviews: list[str], finishes: list[str] | None = None) -> None:
        super().__init__()
        self.scripted, self.finishes = reviews, list(finishes or ['{"edits": [], "complete": true}'])
        self.reviews: list[dict] = []
        self.finished = 0

    def stream(self, **kw):
        if "UPDATE FINISHER" in self._system(kw):
            self.calls.append(kw)
            self.finished += 1
            return _Stream(_text(self.finishes.pop(0) if len(self.finishes) > 1 else self.finishes[0]))
        if "UPDATE REVIEW AND REPAIR" in self._system(kw):
            self.calls.append(kw)
            self.reviews.append(kw)
            reply = self.scripted[min(self.finished, len(self.scripted) - 1)]
            return _Stream(_text(K.review_for_part(reply, kw["messages"][0]["content"])))
        return super().stream(**kw)


def _reviewed_anchors(kw: dict) -> list[str]:
    return K.part_anchors(kw["messages"][0]["content"])


def test_a_grounded_must_fix_is_applied_reaudited_rereviewed_and_proven(
    scene, data_root: Path, firm_config_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_dir, seat = scene
    _upload_pause(monkeypatch)
    delivered_note = {**_must("D1", "no", action="none"), "problem": "a delivered entry reads oddly"}
    first = _reply(_must("N1.c1", FIXED_N1), delivered_note)
    model = _Reviewer([first, _reply()], [K.finish_reply(first)])
    o = _driver(job_dir, firm_config_path, pricing_path, seat, model).run()[0]
    assert o.outcome == "delivered" and o.stage is None, o
    sd = data_root / "example-matter"
    ud = sd / "runs" / "alpha" / "update"
    upd = (ud / "updated-chronology.md").read_text()
    _, ents = B.entries_of(upd)
    # The correction reached the filed document; the delivered entries did not move.
    assert FIXED_N1 in ents[0] and "11:40" not in ents[0]
    assert ents[1:3] == [K.ENTRY_PRE, K.ENTRY_POST]
    # The corrected sentence is a new claim, and the audit graded it on its page.
    rows = [json.loads(x) for x in (sd / "out" / "alpha" / "audit-results.jsonl").read_text().splitlines()]
    fixed_claim = FIXED_N1.replace(" (Exhibit 2 - p. 1)", "")
    assert any(str(r.get("claim")).endswith(fixed_claim) and r.get("verdict") == "SUPPORTED" for r in rows)
    # Two reviews: the second read only the edited entry and its neighbour.
    assert len(model.reviews) == 2 and _reviewed_anchors(model.reviews[1]) == ["N1", "N2"]
    rec = json.loads((ud / "review_repair.json").read_text())
    assert rec["status"] == "clean" and [r["round"] for r in rec["rounds"]] == [1, 2]
    by_anchor = {f["anchor"]: f for f in rec["rounds"][0]["findings"]}
    assert by_anchor["N1.c1"]["outcome"] == "applied" and by_anchor["N1.c1"]["removed"]
    assert by_anchor["D1"]["outcome"].startswith("carried")
    # The proofs ran on the corrected text and held (proof 10 reads the removed time as named).
    assert json.loads((ud / "proofs.json").read_text())["fails"] == []


def test_a_must_fix_that_never_settles_holds_for_smd_and_nothing_is_filed(
    scene, data_root: Path, firm_config_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_dir, seat = scene
    _upload_pause(monkeypatch)
    # Cites a page the finding does not name: never grounded, never applied.
    ungrounded = _must("N2.c1", "MRI showed a disc protrusion measuring 4 mm at that level. (Exhibit 2 - p. 1)", page=2)
    model = _Reviewer([_reply(ungrounded)], [K.finish_reply(_reply(ungrounded))])
    o = _driver(job_dir, firm_config_path, pricing_path, seat, model).run()[0]
    assert o.outcome == "held" and o.stage == "update_review", o
    assert o.reason.startswith("update_held:")
    assert holds.classify("medchron", o.reason)[0] == holds.SMD_FINISHING
    assert seat.sent == [] and seat.deleted == []
    # The finisher settled nothing: no progress, held after the first review.
    assert len(model.reviews) == 1
    ud = data_root / "example-matter" / "runs" / "alpha" / "update"
    rec = json.loads((ud / "review_repair.json").read_text())
    assert rec["status"] == "held" and [f["anchor"] for f in rec["open"]] == ["N2.c1"]
    assert "4 mm" not in (data_root / "example-matter" / "runs" / "alpha" / "final-chronology.md").read_text()


# ---- the $0 half ----------------------------------------------------------------
AUDIT_DOC = (
    "Alpha Example - Medical Chronology\n\n## Treatment Timeline\n\n## Diagnostic Highlights\n\n"
    "## Medical Chronology\n\n"
    "Diagnosis code M50.20 recorded on 02/10/2026. (Exhibit 2 - p. 2)\n\n"
    "02/10/2026\nExample Imaging | Medical Diagnoses\n\nMedical Diagnoses\n"
    "MRI showed a disc protrusion measuring 3 mm at the fifth and sixth level on the right. (Exhibit 2 - p. 2)\n\n"
    "02/12/2026\nExample Therapy | All Other Information\n\nAll Other Information\n"
    "Therapy visit for the neck with range of motion measured at 40 degrees. (Exhibit 3 - p. 1)\n\n"
    "## Exhibit List\n"
)
KEEP, HEADS = {2, 3}, K.HEADINGS


def _f(**kw) -> RR.Finding:
    base = dict(
        severity="MUST",
        klass="2 sides",
        anchor="N1.c1",
        exhibit=2,
        page=2,
        problem="p",
        action="replace",
        corrected="",
        reason="r",
    )
    return RR.Finding(**{**base, **kw})


def test_a_correction_must_cite_the_page_it_names_and_stay_auditable() -> None:
    ok = "MRI showed a disc protrusion measuring 3 mm at the fifth and sixth level on the left. (Exhibit 2 - p. 2)"
    bad = [
        ok.replace("p. 2", "p. 3"),  # another page than the finding names
        ok.replace(" (Exhibit 2 - p. 2)", ""),  # no citation
        "Left sided. (Exhibit 2 - p. 2)",  # too short to be audited
    ]
    for text in bad:
        f = _f(corrected=text)
        md, edited = RR.apply(AUDIT_DOC, [f], KEEP, HEADS)
        assert md == AUDIT_DOC and edited == set() and f.outcome != "applied", text
    f = _f(corrected=ok)
    md, edited = RR.apply(AUDIT_DOC, [f], KEEP, HEADS)
    assert f.outcome == "applied" and ok in md and "on the right" not in md and edited == {0}
    assert "Diagnosis code M50.20 recorded on 02/10/2026. (Exhibit 2 - p. 2)" in md


def test_a_finding_on_a_delivered_entry_is_never_applied() -> None:
    f = _f(anchor="D1", action="replace", corrected="anything at all that is long enough. (Exhibit 2 - p. 2)")
    md, _ = RR.apply(AUDIT_DOC, [f], KEEP, HEADS)
    assert md == AUDIT_DOC and f.outcome.startswith("carried")


def test_a_redate_carries_the_diagnosis_claim_in_its_grammar_and_names_the_moved_values() -> None:
    f = _f(anchor="N1", action="redate", corrected="02/11/2026")
    page = {(2, 2): "MRI cervical spine. Date of service: 02/11/2026"}
    md, edited = RR.apply(AUDIT_DOC, [f], KEEP, HEADS, lambda ex, p: page.get((ex, p)))
    assert f.outcome == "applied" and edited == {0}
    _, ents = B.entries_of(md)
    assert ents[0].startswith("02/11/2026\nExample Imaging")
    assert "Diagnosis code M50.20 recorded on 02/11/2026. (Exhibit 2 - p. 2)" in md
    assert "3" in f.removed
    # A redate grounded on a page the entry does not cite is not applied.
    g = _f(anchor="N1", action="redate", corrected="02/11/2026", page=5)
    assert RR.apply(AUDIT_DOC, [g], KEEP, HEADS)[0] == AUDIT_DOC


def test_an_entry_drop_never_leaves_a_new_exhibit_uncited_and_a_should_drop_is_only_recorded() -> None:
    f = _f(anchor="N2", action="drop_entry", exhibit=3, page=1, reason="repeats a delivered visit")
    md, _ = RR.apply(AUDIT_DOC, [f], KEEP, HEADS)
    assert md == AUDIT_DOC and "uncited" in f.outcome
    g = _f(severity="SHOULD", action="drop")
    assert RR.apply(AUDIT_DOC, [g], KEEP, HEADS)[0] == AUDIT_DOC and "recorded" in g.outcome


def test_a_reply_that_is_not_one_complete_json_object_is_never_read_as_clean() -> None:
    for text in ("no findings", '{"findings": []}', '{"findings": "none", "complete": true}'):
        with pytest.raises(RR.ReplyUnparsed):
            RR.parse_reply(text)
    got = RR.parse_reply('{"findings": [7, {"severity": "maybe", "anchor": "N1"}], "complete": true}')
    assert [f.severity for f in got] == ["MUST", "MUST"] and all(f.outcome for f in got)


def test_a_part_that_runs_out_is_split_by_pages_then_entries() -> None:
    from medchron.update import review_parts as RP

    class _R:
        def __init__(self, text: str, stop: str) -> None:
            self.text, self.stop_reason, self.usage = text, stop, None

    seen: list[tuple[list[str], list[str]]] = []

    class _Door:
        def call(self, stage, **kw):
            content = kw["messages"][0]["content"]
            pages = [b["text"] for b in content if b.get("text", "").startswith("Exhibit 2 p.")]
            seen.append((K.part_anchors(content), pages))
            return _R("", "max_tokens") if len(pages) > 1 else _R(_reply(), "end_turn")

    class _SR:
        doorway = _Door()
        unit = type("U", (), {"client_name": "Alpha Example", "dob": None})()
        job = type("J", (), {"incident_date": "2026-01-15"})()
        log = staticmethod(lambda _m: None)

    ents = RR.anchor_entries(AUDIT_DOC, KEEP, HEADS)
    c = UR.Ctx(
        _SR(), "m", "s", "f", lambda _e, _p: {"type": "image"}, lambda _e, _p: None, lambda _x: 2, lambda _a, _b: []
    )  # type: ignore[arg-type]
    part = RP.Part("pages", ents[:1], 2, [1, 2])
    assert UR._review_part(c, part, ents, [], "p1") == []
    assert [len(p) for _, p in seen] == [2, 1, 1]  # split at once into one page each


def test_proof_10_still_fails_a_value_the_review_removed_without_naming_it(
    scene, data_root: Path, firm_config_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The falsifier for the proof-10 seam: the same correction, with the
    review's named values withheld, fails the update at the proofs."""
    job_dir, seat = scene
    _upload_pause(monkeypatch)
    monkeypatch.setattr(RR, "named_values", lambda _rec: set())
    first = _reply(_must("N1.c1", FIXED_N1))
    model = _Reviewer([first, _reply()], [K.finish_reply(first)])
    o = _driver(job_dir, firm_config_path, pricing_path, seat, model).run()[0]
    assert o.outcome == "failed" and o.stage == "update_proofs", o
    assert seat.sent == []
