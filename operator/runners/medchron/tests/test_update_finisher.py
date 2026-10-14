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
