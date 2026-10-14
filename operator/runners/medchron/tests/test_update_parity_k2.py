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
from typing import Any

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


class _R:
    def __init__(self, text: str) -> None:
        self.text, self.stop_reason, self.usage = text, "end_turn", None


class _Door:
    def __init__(self, *replies: str) -> None:
        self.replies, self.seen = list(replies), []

    def call(self, _stage: str, **kw: Any) -> _R:
        self.seen.append([b.get("text", "") for b in kw["messages"][0]["content"] if b.get("type") == "text"])
        return _R(self.replies.pop(0) if len(self.replies) > 1 else self.replies[0])


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


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entries: list[str], *replies: str):
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
        doorway=_Door(*replies),
        logged=[],
    )
    sr.log = sr.logged.append
    monkeypatch.setattr(UR.prompts, "load", lambda *_a: "UPDATE REVIEW AND REPAIR")
    monkeypatch.setattr(UR.audit_loop, "run", lambda _sr: 0)

    def images(ex: int, page: int) -> dict | None:
        return {"type": "image", "page": f"{ex}:{page}"} if 1 <= page <= sizes.get(ex, 0) else None

    rc = UR.run(sr, images=images, evidence=lambda _e, _p: None)  # type: ignore[arg-type]
    return rc, sr, doc.read_text()


def test_the_review_sees_the_uncited_pages_and_adds_what_the_entry_omits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rc, sr, md = _run(tmp_path, monkeypatch, [REPORT, OTHER], _add(4, "Medical Diagnoses\n" + ADDED), CLEAN)
    first = "\n".join(sr.doorway.seen[0])
    for p in (3, 4, 5, 6):
        assert f"Exhibit 2 p. {p} (uncited):" in first
    assert "Exhibit 2 p. 7" not in first and "UNCITED PAGES OF THE SAME DOCUMENT" in first
    assert rc == 0, sr.logged
    _, ents = B.entries_of(md)
    assert ents[0].endswith("Medical Diagnoses\n" + ADDED)
    rec = json.loads((sr.slug_dir / "runs" / "alpha" / "update" / "review_repair.json").read_text())
    assert rec["status"] == "clean" and rec["rounds"][0]["findings"][0]["outcome"] == "applied"


def test_the_window_stops_at_a_page_another_entry_cites_and_an_add_on_an_unshown_page_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    later = (
        "09/01/2026\nSample Imaging | Medical Diagnoses\n\nMedical Diagnoses\n"
        "Lumbar imaging was ordered and the order form lists two views. (Exhibit 2 - p. 5)"
    )
    bad = ADDED.replace("p. 4", "p. 6")
    rc, sr, md = _run(tmp_path, monkeypatch, [REPORT, later, OTHER], _add(6, bad))
    first = "\n".join(sr.doorway.seen[0])
    assert "Exhibit 2 p. 3 (uncited):" in first and "Exhibit 2 p. 4 (uncited):" in first
    # p.5 belongs to the next entry: N1's window ends at p.4, and p.6 is shown only after N2's p.5.
    assert "Exhibit 2 p. 5 (uncited)" not in first
    n1 = first.split("=== N2 (ADDED) ===")[0]
    assert "Exhibit 2 p. 4 (uncited):" in n1 and "Exhibit 2 p. 6" not in n1
    assert rc == 2 and bad not in md
    rec = json.loads((sr.slug_dir / "runs" / "alpha" / "update" / "review_repair.json").read_text())
    assert rec["open"] and "not an uncited page" in rec["rounds"][0]["findings"][0]["outcome"]


def test_extra_pages_are_capped_per_entry() -> None:
    from medchron.update import review_repair as RR

    md = (
        "Alpha Example - Medical Chronology\n\n## Treatment Timeline\n\n## Diagnostic Highlights\n\n"
        "## Medical Chronology\n\n" + REPORT + "\n\n## Exhibit List\n"
    )
    ent = RR.anchor_entries(md, {2}, K.HEADINGS)[0]
    c = UR.Ctx(None, "m", "s", lambda _ex, p: {} if p <= 40 else None)  # type: ignore[arg-type]
    assert UR._extra(c, ent, ent.cites(), {2}) == [(2, p) for p in range(3, 3 + UR.EXTRA_PAGES)]
    assert UR.EXTRA_PAGES == 6
