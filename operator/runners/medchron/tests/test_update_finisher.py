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
        verify=verify,
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


def test_a_duplicate_whose_content_exists_nowhere_else_is_kept_and_the_job_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = N2_COPY.replace("4 mm", "6 mm")
    dup = _review(_finding("N2", "4 duplicate", "N2 repeats N1"))
    drop = _edits({"finding": "F1", "anchor": "N2", "action": "drop_entry", "exhibit": 2, "page": 1, "survivor": "N1"})
    rc, sr, md = _run(tmp_path, monkeypatch, [N1, other, LATER], [dup], [drop])
    assert rc == 2 and other in md
    assert "does not carry 6" in _record(sr)["open"][0]["outcome"]


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


# (e) ----------------------------------------------------------------------------
def test_the_loop_stops_when_a_review_finds_no_fewer_must_fix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    good = "Lumbar MRI showed a disc protrusion of 5 mm at the fourth and fifth lumbar level. (Exhibit 2 - p. 1)"
    r1 = _review(
        _finding("N1.c1", "1 wrong value", "size misread"), _finding("N2.c1", "3 truncated", "cut off", exhibit=3)
    )
    r2 = _review(
        _finding("N1.c1", "1 wrong value", "size still misread"), _finding("N2.c1", "3 truncated", "cut off", exhibit=3)
    )
    fix = _edits(
        {"finding": "F1", "anchor": "N1.c1", "action": "replace", "exhibit": 2, "page": 1, "corrected": good},
        unresolved=[{"finding": "F2", "why": "the page does not show the rest"}],
    )
    rc, sr, md = _run(tmp_path, monkeypatch, [N1, LATER], [r1, r2], [fix])
    assert rc == 2
    assert _review_rounds(sr) == {"r1", "r2"} and UR.ROUNDS > 2  # stopped at no progress, not at the cap
    assert any("no fewer than the last review" in m for m in sr.logged)


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
    delivered = [N1.replace("08/27/2026", "08/26/2026"), N1.replace("08/27/2026", "07/01/2026")]
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


def test_the_last_reviews_findings_are_held_as_not_finished_with_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    good = "Lumbar MRI showed a disc protrusion of 5 mm at the fourth and fifth lumbar level. (Exhibit 2 - p. 1)"
    r1 = _review(_finding("N1.c1", "1 wrong value", "size misread"))
    fix = _edits({"finding": "F1", "anchor": "N1.c1", "action": "replace", "exhibit": 2, "page": 1, "corrected": good})
    rc, sr, _md = _run(tmp_path, monkeypatch, [N1, LATER], [r1, r1], [fix])
    assert rc == 2
    assert [f["outcome"] for f in _record(sr)["open"]] == [
        "not finished: no progress: 1 must-fix, no fewer than the last review"
    ]


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


def test_a_converging_loop_goes_past_four_reviews_and_finishes_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Five must-fix, one settled per round: the loop is still converging at
    round 4, so it goes on (to at most MAX_ROUNDS) and ends clean at round 6
    instead of holding at round 4 with one open."""
    sent = "Lumbar MRI showed a disc protrusion of {} mm at the fourth and fifth lumbar level. (Exhibit 2 - p. {})"
    head = "08/{:02d}/2026\nSample Imaging Center | Medical Diagnoses\n\nMedical Diagnoses\n"
    entries = [head.format(20 + i) + sent.format(9, 1) for i in range(5)]
    reviews, finishes = [], []
    for r in range(5):
        reviews.append(_review(*[_finding(f"N{i + 1}.c1", "1 wrong value", f"misread {i}") for i in range(r, 5)]))
        fix = {"finding": "F1", "anchor": f"N{r + 1}.c1", "action": "replace", "exhibit": 2, "page": 1}
        finishes.append(_edits({**fix, "corrected": sent.format(4, 1)}))
    reviews.append(K.CLEAN_REVIEW)
    rc, sr, md = _run(tmp_path, monkeypatch, entries, reviews, finishes)
    assert rc == 0, [f["outcome"] for f in _record(sr)["open"]]
    assert _review_rounds(sr) == {f"r{i}" for i in range(1, 7)}
    assert sent.format(9, 1) not in md
    assert UR.ROUNDS == 4 and UR.MAX_ROUNDS == 6


def test_past_four_reviews_a_loop_that_stops_converging_holds() -> None:
    assert UR._limit(5, 3, 3, 1).startswith("no progress")
    assert UR._limit(4, 2, 3, 0) == "the review limit was reached at round 4"  # falling, but nothing settled
    assert UR._limit(4, 2, 3, 1) == ""  # falling and settling: go on
    assert UR._limit(6, 1, 2, 1) == "the review limit was reached at round 6"
