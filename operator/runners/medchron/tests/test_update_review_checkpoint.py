"""Parity F3: the update review checkpoints every completed round.

A resumed real update (2026-10-14) restarted ``update_review`` from round 1
after a connection error and paid again for every round it had finished,
then failed again. Each completed round now leaves its document, record,
next targets and must-fix count; a rerun (or the requeue of a job whose
network failed) over the same composed entries continues from the next
round. Every value is invented; the test fails on the code before F3.
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

V = "Lumbar MRI showed a disc protrusion of {} mm at the fourth and fifth lumbar level. (Exhibit 2 - p. {})"
ENTRY = "08/{:02d}/2026\nSample Imaging Center | Medical Diagnoses\n\nMedical Diagnoses\n" + V


def _entry(day: int, mm: int, page: int) -> str:
    return ENTRY.format(day, mm, page)


def _finding(anchor: str, problem: str, page: int) -> dict[str, Any]:
    return {
        "severity": "MUST",
        "class": "1 wrong value",
        "anchor": anchor,
        "exhibit": 2,
        "page": page,
        "problem": problem,
    }


def _fix(anchor: str, mm: int, page: int) -> dict[str, Any]:
    return {
        "finding": "F1",
        "anchor": anchor,
        "action": "replace",
        "exhibit": 2,
        "page": page,
        "corrected": V.format(mm, page),
    }


def _reply(key: str, items: list[dict[str, Any]]) -> str:
    return json.dumps({key: items, "unresolved": [], "complete": True})


class _Dies(K.ScriptedDoor):
    """The scripted doorway, whose connection fails at the first review call
    of round `at`."""

    def __init__(self, reviews: list[str], finishes: list[str], at: int) -> None:
        super().__init__(reviews, finishes)
        self.at = at

    def call(self, stage: str, **kw: Any):
        if f"-r{self.at}-" in str(kw.get("custom_id")) and kw.get("system") != "update-finish-system":
            raise ConnectionError("the connection to the model API dropped")
        return super().call(stage, **kw)


def _scene(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entries: list[str]) -> tuple[SimpleNamespace, Path]:
    sd = tmp_path / "matter"
    ud = sd / "runs" / "alpha" / "update"
    ud.mkdir(parents=True)
    out = sd / "out" / "alpha"
    out.mkdir(parents=True)
    (out / "Exhibit 2 - Sample.pdf").write_bytes(b"%PDF")
    (ud / "base.md").write_text(K.base_md())
    (ud / "new_entries.md").write_text("\n\n".join(entries) + "\n")
    doc = sd / "runs" / "alpha" / "final-chronology.md"
    doc.write_text(
        "Alpha Example - Medical Chronology\n\n## Treatment Timeline\n\n## Diagnostic Highlights\n\n"
        "## Medical Chronology\n\n" + "\n\n".join(entries) + "\n\n## Exhibit List\n"
    )
    sr = SimpleNamespace(
        slug_dir=sd,
        unit=SimpleNamespace(unit="alpha", client_name="Alpha Example", dob=None),
        job=SimpleNamespace(incident_date=K.INCIDENT, path=sd / "job" / "job.json"),
        cfg=SimpleNamespace(get=lambda *a: {"review": "m"} if a[-1] == "tiers" else sorted(K.HEADINGS)),
        logged=[],
    )
    sr.log = sr.logged.append
    monkeypatch.setattr(UR.prompts, "load", lambda name, _cfg: name)
    monkeypatch.setattr(UR.audit_loop, "run", lambda _sr: 0)
    return sr, doc


def _go(sr: SimpleNamespace) -> int:
    return UR.run(
        sr,  # type: ignore[arg-type]
        images=lambda ex, p: {"type": "image", "page": f"{ex}:{p}"} if p <= 3 else None,
        evidence=lambda _e, _p: None,
        verify=lambda _a, _b: [],
        npages=lambda _x: 3,
    )


def test_a_review_killed_after_round_two_resumes_at_round_three_without_paying_rounds_one_and_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entries = [_entry(1, 9, 1), _entry(2, 8, 2), _entry(3, 7, 3)]
    reviews = [
        _reply("findings", [_finding("N1.c1", "a", 1), _finding("N2.c1", "b", 2), _finding("N3.c1", "c", 3)]),
        _reply("findings", [_finding("N2.c1", "b", 2), _finding("N3.c1", "c", 3)]),
        _reply("findings", [_finding("N3.c1", "c", 3)]),
        _reply("findings", []),
    ]
    finishes = [
        _reply("edits", [_fix("N1.c1", 4, 1)]),
        _reply("edits", [_fix("N2.c1", 5, 2)]),
        _reply("edits", [_fix("N3.c1", 6, 3)]),
    ]
    sr, doc = _scene(tmp_path, monkeypatch, entries)
    sr.doorway = _Dies(reviews, finishes, at=3)
    with pytest.raises(ConnectionError):
        _go(sr)
    first = {kw["custom_id"].split("-")[1] for kw in sr.doorway.seen}
    assert first == {"r1", "r2"}  # rounds 1 and 2 completed; round 3's first review call died
    # The requeue: a fresh process, a fresh doorway, the same composed entries.
    sr.doorway = K.ScriptedDoor(reviews, finishes[2:])
    assert _go(sr) == 0, sr.logged
    again = [kw["custom_id"] for kw in sr.doorway.seen]
    assert again and not any(c.split("-")[1] in ("r1", "r2") for c in again)  # rounds 1-2 are not re-called
    assert any("resuming after round 2" in m for m in sr.logged)
    ents = B.entries_of(doc.read_text())[1]
    assert [V.format(m, p) in e for e, m, p in zip(ents, (4, 5, 6), (1, 2, 3))] == [True, True, True]
    rec = json.loads((sr.slug_dir / "runs" / "alpha" / "update" / "review_repair.json").read_text())
    assert rec["status"] == "clean" and [r["round"] for r in rec["rounds"]] == [1, 2, 3, 4]
    assert not (sr.slug_dir / "runs" / "alpha" / "update" / UR.CHECKPOINT).exists()


def test_a_new_collect_is_a_new_review_never_a_resumed_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entries = [_entry(1, 9, 1), _entry(2, 8, 2)]
    reviews = [_reply("findings", [_finding("N1.c1", "a", 1), _finding("N2.c1", "b", 2)]), _reply("findings", [])]
    sr, doc = _scene(tmp_path, monkeypatch, entries)
    sr.doorway = _Dies(reviews, [_reply("edits", [_fix("N1.c1", 4, 1)])], at=2)
    with pytest.raises(ConnectionError):
        _go(sr)
    ud = sr.slug_dir / "runs" / "alpha" / "update"
    (ud / "new_entries.md").write_text("recomposed\n")  # compose ran again: another review
    sr.doorway = K.ScriptedDoor([_reply("findings", [])])
    assert _go(sr) == 0
    assert {kw["custom_id"].split("-")[1] for kw in sr.doorway.seen} == {"r1"}
