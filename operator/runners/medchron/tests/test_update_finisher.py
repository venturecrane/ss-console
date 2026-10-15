"""Parity F2: the update's independent review and finisher loop
(``stages/update_review.py``, ``update/finisher.py``).

The laptop's chronology updates were delivered clean because, after the
pipeline, an independent review read the entries against the page images and
an agent corrected every must-fix finding, looping until the review found
nothing must-fix. The lane's own review could only repair claim anchors, so a
duplicate entry, a wrong provider line or an omitted opinion held the job.
Now the review only reports, and a finisher edits any text of an added entry;
every sentence it writes is audited, and an edit the audit does not support is
undone. Every value here is invented; each test fails on the code before F2.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import update_testkit as K
from medchron.stages import update_review as UR
from medchron.update import body as B

N1 = (
    "08/27/2026\nSample Imaging Center | Medical Diagnoses\n\nMedical Diagnoses\n"
    "Lumbar MRI showed a disc protrusion of 4 mm at the fourth and fifth lumbar level. (Exhibit 2 - p. 1)"
)
N2_COPY = (
    "08/27/2026\nSample Imaging | Medical Diagnoses\n\nMedical Diagnoses\n"
    "Lumbar MRI recorded a disc protrusion of 4 mm at the fourth and fifth lumbar level. (Exhibit 2 - p. 1)"
)
LATER = (
    "09/10/2026\nSample Therapy | All Other Information\n\nAll Other Information\n"
    "Therapy evaluation recorded lumbar flexion limited to forty degrees. (Exhibit 3 - p. 1)"
)
OPINION = "The physician stated the lumbar injury was caused by the collision of record. (Exhibit 2 - p. 3)"


def _finding(anchor: str, klass: str, problem: str, page: int = 1, exhibit: int = 2) -> dict[str, Any]:
    return {
        "severity": "MUST",
        "class": klass,
        "anchor": anchor,
        "exhibit": exhibit,
        "page": page,
        "problem": problem,
        "page_says": "read off the page",
    }


def _review(*findings: dict[str, Any]) -> str:
    return json.dumps({"findings": list(findings), "complete": True})


def _edits(*edits: dict[str, Any], unresolved: list | None = None) -> str:
    return json.dumps({"edits": list(edits), "unresolved": unresolved or [], "complete": True})


def _run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entries: list[str],
    reviews: list[str],
    finishes: list[str],
    *,
    pages: dict[tuple[int, int], str] | None = None,
    unsupported: list[str] | None = None,
    grader: Any = None,
) -> tuple[int, SimpleNamespace, str]:
    sd = tmp_path / "matter"
    ud = sd / "runs" / "alpha" / "update"
    ud.mkdir(parents=True, exist_ok=True)
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
    sr = SimpleNamespace(
        slug_dir=sd,
        unit=SimpleNamespace(unit="alpha", client_name="Alpha Example", dob=None),
        job=SimpleNamespace(incident_date=K.INCIDENT, path=sd / "job" / "job.json"),
        cfg=SimpleNamespace(get=lambda *a: {"review": "m"} if a[-1] == "tiers" else sorted(K.HEADINGS)),
        doorway=K.ScriptedDoor(reviews, finishes),
        logged=[],
        audited=[],
    )
    sr.log = sr.logged.append
    monkeypatch.setattr(UR.prompts, "load", lambda name, _cfg: name)
    monkeypatch.setattr(UR.audit_loop, "run", lambda _sr: 0)
    sizes = {2: 3, 3: 1}

    def verify(_before: str, after: str) -> list[dict[str, Any]]:
        sr.audited.append(after)
        return [{"claim": c} for c in unsupported or [] if c in after]

    rc = UR.run(
        sr,  # type: ignore[arg-type]
        images=lambda ex, p: {"type": "image", "page": f"{ex}:{p}"} if p <= sizes.get(ex, 0) else None,
        evidence=lambda ex, p: (pages or {}).get((ex, p)),
        verify=grader or verify,
        npages=lambda ex: sizes.get(ex, 0),
    )
    return rc, sr, doc.read_text()


def _record(sr: SimpleNamespace) -> dict[str, Any]:
    return json.loads((sr.slug_dir / "runs" / "alpha" / "update" / "review_repair.json").read_text())


def _review_rounds(sr: SimpleNamespace) -> set[str]:
    return {kw["custom_id"].split("-")[1] for kw in sr.doorway.review_calls}


# (a) ----------------------------------------------------------------------------
def test_a_duplicate_entry_is_removed_and_its_content_survives_in_the_entry_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dup = _review(_finding("N2", "4 duplicate", "N2 composes the same page as N1"))
    drop = _edits({"finding": "F1", "anchor": "N2", "action": "drop_entry", "exhibit": 2, "page": 1, "survivor": "N1"})
    rc, sr, md = _run(tmp_path, monkeypatch, [N1, N2_COPY, LATER], [dup, K.CLEAN_REVIEW], [drop])
    assert rc == 0, [f["outcome"] for f in _record(sr)["open"]]
    _, ents = B.entries_of(md)
    assert ents == [N1, LATER]  # the copy is gone; the visit and its 4 mm stay in N1
    rec = _record(sr)
    assert rec["status"] == "clean" and rec["rounds"][0]["findings"][0]["outcome"] == "applied"


# (b) ----------------------------------------------------------------------------
def test_a_wrong_header_date_and_provider_are_corrected_from_the_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wrong = N1.replace("08/27/2026\nSample Imaging Center", "09/27/2026\nWrong Clinic")
    page = {(2, 1): "SAMPLE IMAGING CENTER\nMRI LUMBAR SPINE\nDate of service: 08/27/2026"}
    found = _review(
        _finding("N2", "1 wrong date", "the date line is not the date of service"),
        _finding("N2", "1 wrong provider", "the provider line names another provider"),
    )
    fix = _edits(
        {"finding": "F1", "anchor": "N2", "action": "redate", "exhibit": 2, "page": 1, "corrected": "08/27/2026"},
        {
            "finding": "F2",
            "anchor": "N2",
            "action": "provider",
            "exhibit": 2,
            "page": 1,
            "corrected": "Sample Imaging Center",
        },
    )
    rc, sr, md = _run(tmp_path, monkeypatch, [LATER, wrong], [found, K.CLEAN_REVIEW], [fix], pages=page)
    assert rc == 0, [f["outcome"] for f in _record(sr)["open"]]
    _, ents = B.entries_of(md)
    assert ents == [N1, LATER]  # corrected and back in date order


def test_a_provider_the_page_does_not_state_is_never_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = {(2, 1): "SAMPLE IMAGING CENTER\nMRI LUMBAR SPINE"}
    found = _review(_finding("N1", "1 wrong provider", "the provider line names another provider"))
    fix = _edits(
        {"finding": "F1", "anchor": "N1", "action": "provider", "exhibit": 2, "page": 1, "corrected": "Other Clinic"}
    )
    rc, sr, md = _run(tmp_path, monkeypatch, [N1], [found], [fix], pages=page)
    assert rc == 2 and "Other Clinic" not in md
    assert "does not state Other Clinic" in _record(sr)["open"][0]["outcome"]


# (c) ----------------------------------------------------------------------------
def test_an_omitted_opinion_is_added_from_a_page_no_entry_cites_and_audited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    found = _review(_finding("N1", "2 omitted opinion", "the causation opinion on p. 3 is left out", page=3))
    add = _edits(
        {
            "finding": "F1",
            "anchor": "N1",
            "action": "add",
            "exhibit": 2,
            "page": 3,
            "corrected": "Medical Diagnoses\n" + OPINION,
        }
    )
    rc, sr, md = _run(tmp_path, monkeypatch, [N1, LATER], [found, K.CLEAN_REVIEW], [add])
    assert rc == 0, [f["outcome"] for f in _record(sr)["open"]]
    first = "\n".join(b.get("text", "") for b in sr.doorway.review_calls[0]["messages"][0]["content"])
    assert "Exhibit 2 p. 3 (cited by no added entry):" in first  # the review read the uncited page
    assert B.entries_of(md)[1][0].endswith(OPINION)
    assert sr.audited and OPINION in sr.audited[0]  # the added sentence went to the audit


# (d) ----------------------------------------------------------------------------
def test_a_finisher_edit_the_audit_does_not_support_is_reverted_and_the_job_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = "Lumbar MRI showed a disc protrusion of 7 mm at the fourth and fifth lumbar level. (Exhibit 2 - p. 1)"
    found = _review(_finding("N1.c1", "1 wrong value", "the protrusion size is misread"))
    fix = _edits({"finding": "F1", "anchor": "N1.c1", "action": "replace", "exhibit": 2, "page": 1, "corrected": bad})
    rc, sr, md = _run(tmp_path, monkeypatch, [N1, LATER], [found], [fix], unsupported=[bad.split(" (Exhibit")[0]])
    assert rc == 2
    assert "7 mm" not in md and N1 in md  # reverted
    rec = _record(sr)
    assert rec["status"] == "held" and rec["open"][0]["outcome"].startswith("reverted")
    hold = sr.logged[-1]
    assert hold.startswith("update_held:") and "the protrusion size is misread" in hold


# a copy of a delivered visit whose exhibit nothing else cites -------------------
COPY_OF_D2 = (
    "01/20/2026\nExample Clinic | Medical Diagnoses\n\nMedical Diagnoses\n"
    "Cervical strain after the collision, pain rated 6 of 10 at intake. (Exhibit 3 - p. 1)"
)
NEW_EXHIBITS = [
    {"exhibit": 2, "id": "n2", "title": "Exhibit 2 - Sample Imaging Center (Medical Records)", "pages": 3},
    {"exhibit": 3, "id": "n3", "title": "Exhibit 3 - Hospital Encounters (Medical Records)", "pages": 1},
]


def test_a_copy_of_a_delivered_visit_is_dropped_and_its_exhibit_kept_as_a_refiled_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The only entry citing new Exhibit 3 repeats delivered D2. Before, the
    drop was refused (it would leave Exhibit 3 uncited) and the job held. Now
    the entry is dropped into D2 and Exhibit 3 is recorded as a re-filed copy
    of delivered Exhibit 1: its number kept, not filed again."""
    dup = _review(_finding("N1", "4 duplicate", "N1 repeats the delivered visit D2", exhibit=3))
    drop = _edits({"finding": "F1", "anchor": "N1", "action": "drop_entry", "exhibit": 3, "page": 1, "survivor": "D2"})
    ud = tmp_path / "matter" / "runs" / "alpha" / "update"
    ud.mkdir(parents=True)
    (ud / "new_exhibits.final.json").write_text(json.dumps(NEW_EXHIBITS))
    rc, sr, md = _run(tmp_path, monkeypatch, [COPY_OF_D2, N1], [dup, K.CLEAN_REVIEW], [drop])
    assert rc == 0, [f["outcome"] for f in _record(sr)["open"]]
    assert B.entries_of(md)[1] == [N1]
    rows = json.loads((ud / "new_exhibits.final.json").read_text())
    assert [r.get("refiled") for r in rows] == [None, "Exhibit 1"]


def test_a_refiled_copy_gets_its_exhibit_list_row_and_is_not_an_uncited_exhibit() -> None:
    from medchron.update import assemble as A, collect as C

    nex = [{**NEW_EXHIBITS[0]}, {**NEW_EXHIBITS[1], "refiled": "Exhibit 1"}]
    md, rep = A.assemble(
        K.base_md(),
        C.audit_document(K.base_md(), [N1], nex),
        describe=lambda code, on, text: (code, None, "x"),
        canon=str,
        headings=K.HEADINGS,
        new_exhibits=nex,
    )
    assert rep["new_exhibits_uncited"] == []
    assert "| 3 | Hospital Encounters (Medical Records) (re-filed pages of Exhibit 1; not filed again) |" in md


# the delivered visit a re-filed copy repeats -----------------------------------
def test_the_review_sees_delivered_visits_near_the_entry_and_is_told_a_document_is_mostly_a_copy() -> None:
    """A re-filed copy of a delivered hospital record: most of its pages are
    matched to the delivered exhibit, a few are not, and an entry composed
    from those repeats a delivered visit dated a day off. The review is shown
    the delivered entries within a week and told the document is mostly a
    copy; the copied pages themselves are not shown."""
    from medchron.update import review_parts as RP, review_repair as RR

    copy = N1.replace("(Exhibit 2 - p. 1)", "(Exhibit 2 - p. 10)")
    md = (
        "Alpha Example - Medical Chronology\n\n## Treatment Timeline\n\n## Diagnostic Highlights\n\n"
        "## Medical Chronology\n\n" + copy + "\n\n## Exhibit List\n"
    )
    ents = RR.anchor_entries(md, {2}, K.HEADINGS)
    already = {2: {p: f"Exhibit 1 p. {p + 30}" for p in range(1, 21) if p != 10}}
    far = N1.replace("08/27/2026", "07/01/2026").replace("Sample Imaging Center", "Other Clinic")
    delivered = [N1.replace("08/27/2026", "08/26/2026"), far]  # weeks off and another provider: not shown
    parts = RP.plan(ents, {2}, lambda _x: 20, already)
    assert [p.pages for p in parts] == [[10]]  # only the page an entry cites; the copies are not shown
    blocks = RP.review_content(parts[0], "CLIENT", lambda _e, _p: None, ents, delivered)
    text = "\n".join(b.get("text", "") for b in blocks)
    assert "19 of its 20 pages are copies of delivered Exhibit 1" in text
    assert "=== D1 (DELIVERED) ===" in text and "=== D2 (DELIVERED) ===" not in text  # a day off is near


# F3: only the edit the audit did not support is reverted ------------------------
def test_one_unsupported_edit_is_reverted_and_the_three_supported_edits_stay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A round with four corrections: the audit does not support one, and it
    also grades unsupported a sentence no edit wrote. Before, an untraced
    sentence reverted every edit of the round (58 on a real round); now only
    the traced edit is undone and the untraced sentence is the audit loop's."""
    sent = "Lumbar MRI showed a disc protrusion of {} mm at the fourth and fifth lumbar level. (Exhibit {} - p. {})"
    entry = "08/{:02d}/2026\nSample Imaging Center | Medical Diagnoses\n\nMedical Diagnoses\n" + sent
    where = [(2, 1), (2, 2), (2, 3), (3, 1)]
    entries = [entry.format(20 + i, 9, ex, p) for i, (ex, p) in enumerate(where)]
    fixed = [sent.format(4 + i, ex, p) for i, (ex, p) in enumerate(where)]
    found = _review(
        *[
            _finding(f"N{i + 1}.c1", "1 wrong value", "size misread", page=p, exhibit=ex)
            for i, (ex, p) in enumerate(where)
        ]
    )
    fixes = _edits(
        *[
            {
                "finding": f"F{i + 1}",
                "anchor": f"N{i + 1}.c1",
                "action": "replace",
                "exhibit": ex,
                "page": p,
                "corrected": fixed[i],
            }
            for i, (ex, p) in enumerate(where)
        ]
    )
    untraced = "Sample Imaging Center"  # in the document, written by no edit
    rc, sr, md = _run(
        tmp_path,
        monkeypatch,
        entries,
        [found, K.CLEAN_REVIEW],
        [fixes],
        unsupported=[fixed[0].split(" (Exhibit")[0], untraced],
    )
    ents = B.entries_of(md)[1]
    assert entries[0] in md and fixed[0] not in md  # the unsupported edit is undone
    assert all(f in md for f in fixed[1:])  # the three supported edits stay
    assert len(ents) == 4 and rc == 0


# F4 -----------------------------------------------------------------------------
def test_a_finding_the_finisher_declines_is_held_with_its_reason_never_an_empty_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real update held with 52 findings marked "unknown action ''": the
    last review's findings were never sent to the finisher (the round limit),
    and a finding with no action read as a malformed one. Now a decline says
    why, and a finding the loop did not finish says that."""
    found = _review(
        _finding("N1.c1", "1 wrong value", "size misread"), _finding("N2.c1", "3 truncated", "cut off", exhibit=3)
    )
    declines = _edits(
        {"finding": "F1", "anchor": "N1.c1", "action": "", "reason": "the image is too faint to read the size"},
        {"finding": "F2", "anchor": "N2.c1", "action": "decline", "reason": "the rest is on no page shown"},
    )
    rc, sr, _md = _run(tmp_path, monkeypatch, [N1, LATER], [found], [declines])
    assert rc == 2
    outcomes = [f["outcome"] for f in _record(sr)["open"]]
    assert outcomes == [
        "declined by the finisher: the image is too faint to read the size",
        "declined by the finisher: the rest is on no page shown",
    ]
    assert not any("unknown action" in m for m in sr.logged)
    assert any("OPEN" in m and "too faint" in m for m in sr.logged)


def test_a_handwritten_row_takes_its_provider_from_the_statement_page_of_the_same_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The row cites p. 2 (handwritten, names no one); p. 1 of the same
    document is the typed statement that names the chiropractor. Before, the
    provider edit was refused as grounded on a page the entry does not cite."""
    row = (
        "08/27/2026\nSample Chiropractic (provider not stated in the record) | All Other Information\n\n"
        "All Other Information\nSpinal adjustment of the lumbar spine recorded in the log row. (Exhibit 2 - p. 2)"
    )
    page = {(2, 1): "SAMPLE CHIROPRACTIC\nTreating doctor: Alex Example, D.C.\nSTATEMENT OF ACCOUNT"}
    found = _review(_finding("N1", "1 wrong provider", "the statement names the chiropractor", page=1))
    fix = _edits(
        {
            "finding": "F1",
            "anchor": "N1",
            "action": "provider",
            "exhibit": 2,
            "page": 1,
            "corrected": "Alex Example, D.C.",
        }
    )
    rc, sr, md = _run(tmp_path, monkeypatch, [row], [found, K.CLEAN_REVIEW], [fix], pages=page)
    assert rc == 0, [f["outcome"] for f in _record(sr)["open"]]
    assert B.entries_of(md)[1][0].split("\n")[1] == "Alex Example, D.C. | All Other Information"


def test_a_redate_may_rest_on_an_uncited_page_of_the_entrys_document_that_states_the_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wrong = N1.replace("08/27/2026", "09/27/2026")
    page = {(2, 3): "MRI LUMBAR SPINE REPORT\nDate of exam: 08/27/2026"}
    found = _review(_finding("N1", "1 wrong date", "the report page dates the exam", page=3))
    fix = _edits(
        {"finding": "F1", "anchor": "N1", "action": "redate", "exhibit": 2, "page": 3, "corrected": "08/27/2026"}
    )
    rc, sr, md = _run(tmp_path, monkeypatch, [wrong], [found, K.CLEAN_REVIEW], [fix], pages=page)
    assert rc == 0, [f["outcome"] for f in _record(sr)["open"]]
    assert B.entries_of(md)[1] == [N1]


def _grade(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tiers: dict, verdict: str) -> tuple[dict, Any]:
    """The verification round over one edited sentence, with a fake Round
    that records its grader and writes `verdict` for every claim."""
    from medchron.audit import claims as CL

    seen: dict[str, Any] = {}

    class _Round:
        def __init__(self, _door: Any, model: str, paths: Any, _log: Any, mode: str = "image") -> None:
            seen.update(model=model, mode=mode)
            self.paths = paths

        def execute(self) -> int:
            body = CL.body_of(self.paths.doc.read_text())
            for c in CL.extract_claims(body, {2}):
                CL.append_row(self.paths.results, {**c, "kind": "real", "verdict": verdict, "note": "x"})
            return 0

    monkeypatch.setattr(UR, "Round", _Round)
    out = tmp_path / "matter" / "out" / "alpha"
    out.mkdir(parents=True)
    (out / "Exhibit 2 - Sample.pdf").write_bytes(b"%PDF")
    sr = SimpleNamespace(
        slug_dir=tmp_path / "matter",
        unit=SimpleNamespace(unit="alpha"),
        cfg=SimpleNamespace(get=lambda *a: tiers if a[-1] == "tiers" else "text"),
        doorway=None,
        log=lambda _m: None,
    )
    doc = "Alpha - Medical Chronology\n\n## Medical Chronology\n\n{}\n\n## Exhibit List\n"
    (tmp_path / "matter" / "runs" / "alpha").mkdir(parents=True)
    try:
        got: Any = UR._audit_verify(sr)(doc.format(N1), doc.format(N1.replace("4 mm", "5 mm")))  # type: ignore[arg-type]
    except UR.GradeFailed as exc:
        got = exc
    return seen, got


def test_the_finishers_sentences_are_graded_by_a_verdict_model_never_one_that_refuses_the_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F6. F5 graded with the review tier, claude-opus-5-5, which answers the
    audit's forced verdict tool with HTTP 400: all 46 edits of a real round
    were reverted on ERROR verdicts. The grader is the firm's `verify` tier,
    else the audit tier, on the image."""
    base = {"audit": "audit-tier", "judgment": "claude-opus-5-5", "review": "claude-opus-5-5"}
    seen, _ = _grade(tmp_path, monkeypatch, base, "SUPPORTED")
    assert seen == {"model": "audit-tier", "mode": "image"}
    seen, _ = _grade(tmp_path / "b", monkeypatch, {**base, "verify": "verify-tier"}, "SUPPORTED")
    assert seen["model"] == "verify-tier"
    from medchron import config as config_mod

    data = {
        **K.cfg().data,
        "models": {**K.cfg().data["models"], "tiers": {**K.cfg().data["models"]["tiers"], "verify": "claude-opus-5-5"}},
    }
    errors = config_mod.validate(data)
    assert any("models.tiers.verify" in e and "forced tool_choice" in e for e in errors)


def test_a_grader_refusal_fails_the_stage_resumably_and_reverts_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mass revert, by construction impossible: the grader refuses (an
    HTTP 400 is an ERROR verdict, GradeFailed) and the stage fails resumably
    with the document as the round found it; no finding is marked reverted
    and nothing is held."""
    good = "Lumbar MRI showed a disc protrusion of 5 mm at the fourth and fifth lumbar level. (Exhibit 2 - p. 1)"
    found = _review(_finding("N1.c1", "1 wrong value", "size misread"))
    fix = _edits({"finding": "F1", "anchor": "N1.c1", "action": "replace", "exhibit": 2, "page": 1, "corrected": good})

    def refuse(_before: str, _after: str) -> list:
        raise UR.GradeFailed("1 of the finisher's sentences got no verdict: Error code: 400")

    sd = tmp_path / "matter"
    rc, sr, md = _run(tmp_path, monkeypatch, [N1, LATER], [found], [fix], grader=refuse)
    assert rc == 1
    assert N1 in md and good not in md  # the round's document, untouched
    assert not (sd / "runs" / "alpha" / "update" / "review_repair.json").exists()  # nothing held, nothing settled
    assert any("got no verdict" in m and "a rerun grades again" in m for m in sr.logged)


def test_a_stale_no_verdict_row_is_graded_again_by_the_verify_tier_never_a_refusing_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F8. A run cloned from one graded by claude-opus-5-5 (every call
    refused) carried an ERROR row for a sentence the finisher wrote again; the
    audit resumes any key with a row, so the verify round never graded it and
    the stage failed on the stale refusal although every call went to the
    verify tier. The stale ERROR is now graded again; no verdict call goes to
    a model that refuses the forced tool."""
    from medchron import config as config_mod
    from medchron.audit import claims as CL

    calls: list[str] = []

    class _Round:
        """Grades, like the audit's Round, only the keys it has no verdict for."""

        def __init__(self, _door: Any, model: str, paths: Any, _log: Any, mode: str = "image") -> None:
            self.model, self.paths = model, paths

        def execute(self) -> int:
            rows = CL.read_rows(self.paths.results)
            for c in CL.extract_claims(CL.body_of(self.paths.doc.read_text()), {2}):
                if c["key"] not in CL.done_keys(rows):
                    calls.append(self.model)
                    CL.append_row(self.paths.results, {**c, "kind": "real", "verdict": "SUPPORTED", "note": "x"})
            return 0

    monkeypatch.setattr(UR, "Round", _Round)
    out = tmp_path / "matter" / "out" / "alpha"
    out.mkdir(parents=True)
    (out / "Exhibit 2 - Sample.pdf").write_bytes(b"%PDF")
    doc = "Alpha - Medical Chronology\n\n## Medical Chronology\n\n{}\n\n## Exhibit List\n"
    after = doc.format(N1.replace("4 mm", "5 mm"))
    (claim,) = CL.extract_claims(CL.body_of(after), {2})
    stale = {**claim, "kind": "real", "verdict": "ERROR", "note": "Error code: 400 - tool_choice not supported"}
    CL.append_row(out / "audit-results.jsonl", stale)
    tiers = {"audit": "claude-sonnet-5", "verify": "claude-opus-5", "review": "claude-opus-5-5"}
    sr = SimpleNamespace(
        slug_dir=tmp_path / "matter",
        unit=SimpleNamespace(unit="alpha"),
        cfg=SimpleNamespace(get=lambda *a: tiers if a[-1] == "tiers" else "text"),
        doorway=None,
        log=lambda _m: None,
    )
    (tmp_path / "matter" / "runs" / "alpha").mkdir(parents=True)
    assert UR._audit_verify(sr)(doc.format(N1), after) == []  # type: ignore[arg-type]
    assert calls == ["claude-opus-5"]
    assert not any(m.startswith(config_mod.NO_FORCED_TOOL_MODELS) for m in calls)


def test_a_grade_that_is_no_verdict_fails_the_stage_and_reverts_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seen, got = _grade(tmp_path, monkeypatch, {"audit": "audit-tier"}, "ERROR")
    assert isinstance(got, UR.GradeFailed) and "got no verdict" in str(got)


# F6: the two actions that change which entries the update has ---------------------
PROCEDURE = "Right sacroiliac joint injection was performed under fluoroscopic guidance. (Exhibit 3 - p. 1)"


def test_a_procedure_folded_into_an_office_visit_gets_its_own_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A separate facility's procedure, from its own exhibit, sat inside an
    office visit's entry; the finisher declined with "No available action
    creates an entry". Now `new_entry` builds the procedure's own entry from
    its cited page (dated by that page's text) and the moved sentence is
    dropped from the visit."""
    visit = N1 + "\n\nAll Other Information\n" + PROCEDURE
    proc = "08/13/2026\nSample Surgical Center | Medical Diagnoses\n\nMedical Diagnoses\n" + PROCEDURE
    page = {(3, 1): "SAMPLE SURGICAL CENTER\nPROCEDURE REPORT\nDate of procedure: 08/13/2026"}
    found = _review(_finding("N1.c2", "5 misattributed", "a separate procedure sits in the visit", exhibit=3))
    fix = _edits(
        {"finding": "F1", "anchor": "N1", "action": "new_entry", "exhibit": 3, "page": 1, "corrected": proc},
        {
            "finding": "F1",
            "anchor": "N1.c2",
            "action": "drop",
            "exhibit": 3,
            "page": 1,
            "reason": "moved to its own entry",
        },
    )
    rc, sr, md = _run(tmp_path, monkeypatch, [visit], [found, K.CLEAN_REVIEW], [fix], pages=page)
    assert rc == 0, [f["outcome"] for f in _record(sr)["open"]]
    assert B.entries_of(md)[1] == [proc, N1]  # its own entry, in date order; the visit is the visit again
    assert sr.audited and proc.split("\n", 2)[2] in sr.audited[0]  # the new entry was audited


def test_a_new_entry_the_page_does_not_date_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    visit = N1 + "\n\nAll Other Information\n" + PROCEDURE
    proc = "08/14/2026\nSample Surgical Center | Medical Diagnoses\n\nMedical Diagnoses\n" + PROCEDURE
    page = {(3, 1): "SAMPLE SURGICAL CENTER\nPROCEDURE REPORT\nDate of procedure: 08/13/2026"}
    found = _review(_finding("N1.c2", "5 misattributed", "a separate procedure sits in the visit", exhibit=3))
    fix = _edits({"finding": "F1", "anchor": "N1", "action": "new_entry", "exhibit": 3, "page": 1, "corrected": proc})
    rc, sr, md = _run(tmp_path, monkeypatch, [visit], [found], [fix], pages=page)
    assert rc == 2 and "08/14/2026" not in md
    assert "does not state the new entry's date" in _record(sr)["open"][0]["outcome"]


ORDERS = "Discharge orders listed ice packs to the neck twice daily for 14 days. (Exhibit 3 - p. 1)"


def test_a_copy_of_a_delivered_visit_with_new_content_is_folded_under_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The added entry repeats delivered D2 but its new pages carry orders D2
    lacks; the finisher declined ("delivered entries cannot be edited"). Now
    `append_to_delivered` keeps only those orders, under D2's own date and
    provider line, audited, and assemble folds them into D2."""
    from medchron.update import assemble as A, collect as C, entry_edits as EE

    copy = (
        "01/20/2026\nExample Clinic | Medical Diagnoses\n\nMedical Diagnoses\n"
        "Cervical strain after the collision, pain rated 6 of 10 at intake. (Exhibit 3 - p. 1)\n\n"
        "All Other Information\n" + ORDERS
    )
    found = _review(_finding("N1", "4 duplicate", "N1 repeats delivered D2", exhibit=3))
    fix = _edits(
        {
            "finding": "F1",
            "anchor": "N1",
            "action": "append_to_delivered",
            "exhibit": 3,
            "page": 1,
            "survivor": "D2",
            "corrected": ORDERS,
        }
    )
    rc, sr, md = _run(tmp_path, monkeypatch, [copy], [found, K.CLEAN_REVIEW], [fix])
    assert rc == 0, [f["outcome"] for f in _record(sr)["open"]]
    (fold,) = B.entries_of(md)[1]
    assert EE.is_fold(fold) and fold.endswith(ORDERS) and "pain rated 6 of 10" not in fold
    assert sr.audited and ORDERS.split(" (Exhibit")[0] in sr.audited[0]
    nex = [{"exhibit": 3, "title": "Exhibit 3 - Example Clinic (Medical Records)", "pages": 1}]
    out, _rep = A.assemble(
        K.base_md(),
        C.audit_document(K.base_md(), [fold], nex),
        describe=lambda code, on, text: (code, None, "x"),
        canon=str,
        headings=K.HEADINGS,
        new_exhibits=nex,
    )
    ents = B.entries_of(out)[1]
    assert len(ents) == 2 and ents[1].startswith(K.ENTRY_POST)  # D2, byte for byte, then the fold
    assert ents[1].endswith(ORDERS) and EE.FOLD in ents[1]


def test_a_fold_that_would_lose_a_value_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The problem names no entry, so the code cannot decide the duplicate
    (``update/duplicates.py``) and the finisher's fold is checked: it would
    lose 21, so it is refused, re-raised on the re-check, refused again."""
    copy = "01/20/2026\nExample Clinic | Medical Diagnoses\n\nMedical Diagnoses\n" + ORDERS.replace(
        "14 days", "21 days"
    )
    found = _review(_finding("N1", "4 duplicate", "N1 repeats a delivered visit", exhibit=3))
    fix = _edits(
        {
            "finding": "F1",
            "anchor": "N1",
            "action": "append_to_delivered",
            "exhibit": 3,
            "page": 1,
            "survivor": "D2",
            "corrected": ORDERS,
        }
    )
    rc, sr, md = _run(tmp_path, monkeypatch, [copy], [found], [fix])
    assert rc == 2 and "21 days" in md
    assert "do not carry 21" in _record(sr)["open"][0]["outcome"]
