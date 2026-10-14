"""Parity K2: the update review sees the uncited pages of the document an
added entry composes, and repairs what the entry omits from them.

On a real update (2026-10-14) a 6-page report reached compose as its first
two pages. The added entry cited p.1-2; the review was shown only those
pages, agreed with them, and passed an entry missing the exam, diagnoses,
plan, causation opinion and signer printed on p.3-6. The review now sees the
pages of the same new exhibit that follow the entry's last cited page (up to
the next page another added entry cites, at most EXTRA_PAGES), and an ``add``
grounded on one of those pages puts the omitted fact into the entry, citing
that page. Every value is invented; each test fails on the code before K2.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import update_testkit as K
from medchron.stages import update_review as UR
from medchron.update import body as B

HEAD = "08/27/2026\nPain management provider, name not stated in record | Medical Diagnoses\n\n"
REPORT = (
    HEAD + "Patient Complaints & Limitations\n"
    "History recorded of low back pain radiating to the right leg since the collision. (Exhibit 2 - p. 1-2)"
)
OTHER = (
    "09/10/2026\nSample Therapy | Medical Diagnoses\n\nMedical Diagnoses\n"
    "Therapy evaluation recorded lumbar flexion limited to forty degrees. (Exhibit 3 - p. 1)"
)
ADDED = "Diagnoses recorded on the report include right lumbar radiculopathy. (Exhibit 2 - p. 4)"


def _add(page: int, corrected: str) -> str:
    f = {
        "severity": "MUST",
        "class": "7 whole document",
        "anchor": "N1",
        "exhibit": 2,
        "page": page,
        "problem": "the report's diagnoses page is omitted",
        "action": "add",
        "corrected": corrected,
        "reason": "printed on the uncited page",
    }
    return json.dumps({"findings": [f], "complete": True})


CLEAN = json.dumps({"findings": [], "complete": True})


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entries: list[str], reviews: list[str], finishes: list[str]):
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
    sizes = {2: 6, 3: 2}
    sr = SimpleNamespace(
        slug_dir=sd,
        unit=SimpleNamespace(unit="alpha", client_name="Alpha Example", dob=None),
        job=SimpleNamespace(incident_date=K.INCIDENT, path=sd / "job" / "job.json"),
        cfg=SimpleNamespace(get=lambda *a: {"review": "m"} if a[-1] == "tiers" else sorted(K.HEADINGS)),
        doorway=K.ScriptedDoor(reviews, finishes),
        logged=[],
    )
    sr.log = sr.logged.append
    monkeypatch.setattr(UR.prompts, "load", lambda name, _cfg: name)
    monkeypatch.setattr(UR.audit_loop, "run", lambda _sr: 0)

    def images(ex: int, page: int) -> dict | None:
        return {"type": "image", "page": f"{ex}:{page}"} if 1 <= page <= sizes.get(ex, 0) else None

    rc = UR.run(
        sr,  # type: ignore[arg-type]
        images=images,
        evidence=lambda _e, _p: None,
        verify=lambda _a, _b: [],
        npages=lambda ex: sizes.get(ex, 0),
    )
    return rc, sr, doc.read_text()


def test_the_review_sees_every_page_of_the_document_and_the_finisher_adds_what_the_entry_omits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review = _add(4, "Medical Diagnoses\n" + ADDED)
    rc, sr, md = _run(tmp_path, monkeypatch, [REPORT, OTHER], [review, CLEAN], [K.finish_reply(review)])
    first = "\n".join(b.get("text", "") for b in sr.doorway.review_calls[0]["messages"][0]["content"])
    assert "Exhibit 2 p. 1 (cited by N1):" in first
    for p in (3, 4, 5, 6):
        assert f"Exhibit 2 p. {p} (cited by no added entry):" in first
    assert "Exhibit 2 p. 7" not in first
    assert rc == 0, sr.logged
    _, ents = B.entries_of(md)
    assert ents[0].endswith("Medical Diagnoses\n" + ADDED)
    rec = json.loads((sr.slug_dir / "runs" / "alpha" / "update" / "review_repair.json").read_text())
    assert rec["status"] == "clean" and rec["rounds"][0]["findings"][0]["outcome"] == "applied"


def test_an_add_citing_a_document_the_entry_does_not_cite_is_refused_and_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = ADDED.replace("Exhibit 2 - p. 4", "Exhibit 3 - p. 1")
    review = json.dumps(
        {"findings": [{**json.loads(_add(4, bad))["findings"][0], "exhibit": 3, "page": 1}], "complete": True}
    )
    rc, sr, md = _run(tmp_path, monkeypatch, [REPORT, OTHER], [review], [K.finish_reply(review)])
    assert rc == 2 and bad not in md
    rec = json.loads((sr.slug_dir / "runs" / "alpha" / "update" / "review_repair.json").read_text())
    assert rec["open"] and "not a page of a document the entry cites" in rec["rounds"][0]["findings"][0]["outcome"]


def test_a_long_document_is_read_in_windows_and_its_entries_are_compared_for_duplicates() -> None:
    """Every page of a long document is shown once, in windows; a window no
    entry cites is read with the entry that precedes it; a document whose
    entries span windows gets a text-only duplicate part with all of them
    (the same visit printed twice inside one file is caught only there)."""
    from medchron.update import review_parts as RP, review_repair as RR

    early = REPORT
    late = REPORT.replace("08/27/2026", "08/28/2026").replace("p. 1-2", "p. 45")
    md = (
        "Alpha Example - Medical Chronology\n\n## Treatment Timeline\n\n## Diagnostic Highlights\n\n"
        "## Medical Chronology\n\n" + early + "\n\n" + late + "\n\n## Exhibit List\n"
    )
    ents = RR.anchor_entries(md, {2}, K.HEADINGS)
    parts = RP.plan(ents, {2}, lambda _x: 50)
    pages = [p for x in parts if x.kind == "pages" for p in x.pages]
    assert pages == list(range(1, 51))
    by_window = {x.pages[0]: [e.anchor for e in x.entries] for x in parts if x.kind == "pages"}
    assert by_window == {1: ["N1"], 21: ["N1"], 41: ["N2"]}
    dup = [x for x in parts if x.kind == "duplicates"]
    assert len(dup) == 1 and [e.anchor for e in dup[0].entries] == ["N1", "N2"]
    content = RP.review_content(dup[0], "CLIENT", lambda _e, _p: None, ents, [])
    assert "DUPLICATE CHECK" in content[1]["text"] and late in "\n".join(b["text"] for b in content)
