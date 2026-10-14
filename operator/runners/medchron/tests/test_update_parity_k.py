"""Parity K: the update lane stops fighting itself on handwritten pages.

On a real update (2026-10-14) the handwritten re-date wrote
"(handwritten date, unconfirmed)" and "(log row reads ...)" into entries'
DATE LINES and the map's "[NTD: ... unable to annotate or decipher ...]"
note into their bodies. The update review graded those MUST-FIX as process
wording, its repair could not change a date line, and after four paid rounds
the job held for SMD. Worse, on those rows the review read the page right
where the re-date had guessed wrong, and the ledger's ROW ORDER, which
settles every misread row, was never used.

K1. Process notes go to the job report, never to the firm's text.
K2. A date that cannot be settled is shown as written, marked as a
    handwritten date (a fact about the record), and listed for SMD.
K3. The review's REDATE applies when the cited page's text states the date,
    and the entry moves to its place in date order.
K4. The ledger's row order settles a misread row.

Every value is invented; each test fails on the branch head before parity K.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import update_testkit as K
from medchron.stages import update_collect as UC, update_review as UR
from medchron.update import body as B

PROV = "Sample Chiropractic"
BANNED = ("unconfirmed", "log row reads", "NTD", "unable to decipher")


def _entry(mdy: str, text: str = "Spinal adjustment recorded on the visit sheet.", page: int = 1, ex: int = 2) -> str:
    return f"{mdy}\n{PROV} | Medical Diagnoses\n\nMedical Diagnoses\n{text} (Exhibit {ex} - p. {page})"


def _matter(tmp_path: Path, pages: list[str], doc: str = "n2") -> SimpleNamespace:
    sd = tmp_path / "matter"
    (sd / "text").mkdir(parents=True)
    body = "".join(f"[p.{i}] (machine transcription)\n{t}\n" for i, t in enumerate(pages, 1))
    (sd / "text" / f"{doc}.txt").write_text(body)
    ud = sd / "runs" / "alpha" / "update"
    ud.mkdir(parents=True)
    (ud / "update_plan.json").write_text(json.dumps({"exhibits": []}))
    return SimpleNamespace(
        slug_dir=sd,
        unit=SimpleNamespace(unit="alpha", client_name="Alpha Example", dob=None),
        job=SimpleNamespace(incident_date=K.INCIDENT, path=sd / "job" / "job.json"),
        cfg=None,
        logged=[],
    )


def _shape(sr: SimpleNamespace, entries: list[str], monkeypatch: pytest.MonkeyPatch) -> tuple[list[str], dict]:
    """The real collect shaping (`_shape`) over composed `entries`."""
    monkeypatch.setattr(UC.C, "composed", lambda _rd: entries)
    monkeypatch.setattr(UC.C, "collect", lambda comp, _base, _inc, _canon: (list(comp), {}))
    monkeypatch.setattr(UC, "Canon", lambda _cfg: None)
    rd = sr.slug_dir / "runs" / "alpha"
    return UC._shape(sr, K.base_md(), rd, [{"exhibit": 2, "id": "n2", "title": "Exhibit 2 - Sample"}])  # type: ignore[arg-type]


STATEMENT = "Sample Chiropractic\nPatient statement\n" + "".join(
    f"Date of service {d} Spinal manipulation charges 65.00\n" for d in ("02/13/2026", "02/20/2026")
)


# ---- K1 + K2 --------------------------------------------------------------------
def test_process_notes_never_reach_the_firm_and_an_unsettled_date_is_reported_not_guessed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sr = _matter(tmp_path, ["[handwritten]\nVisit sheet\nAdj C5 2/18/26\nLow back tender", STATEMENT])
    noted = (
        '02/18/2026 (handwritten date, unconfirmed) (log row reads "2-18," no year written)\n'
        f"{PROV} | Medical Diagnoses\n\nMedical Diagnoses\n"
        "Spinal adjustment at C5 with low back tenderness noted. (Exhibit 2 - p. 1)\n\n"
        "All Other Information\n"
        "[NTD: A portion of the records were handwritten and illegible. Therefore, we were unable to annotate or\n"
        "decipher a portion of these records.] (Exhibit 2 - p. 1)"
    )
    only_note = (
        "02/18/2026\n"
        f"{PROV} | All Other Information\n\nAll Other Information\n"
        "Lumbar flexion was measured at 40 degrees [NTD: rest of the line unable to decipher]. (Exhibit 2 - p. 3)\n"
        "[NTD: A portion of the records were handwritten and illegible.] (Exhibit 2 - p. 4)"
    )
    entries, rep = _shape(sr, [noted, only_note], monkeypatch)
    text = "\n\n".join(entries)
    for w in BANNED:
        assert w not in text, w
    # Legible text stays with its note gone; a page only the note cited stays cited, as a fact of the record.
    assert "Lumbar flexion was measured at 40 degrees. (Exhibit 2 - p. 3)" in text
    assert "Portions of this record are handwritten and not legible. (Exhibit 2 - p. 4)" in text
    assert "Spinal adjustment at C5 with low back tenderness noted. (Exhibit 2 - p. 1)" in text
    # Two statement dates bracket the reading: nothing settles it, so it is NOT guessed.
    assert entries[0].startswith("02/18/2026 (handwritten date)\n") and not any(
        e.startswith(("02/13/2026", "02/20/2026")) for e in entries
    )
    # SMD reads all of it in the report.
    lines = UC.report_lines(rep)
    assert any("NOT SETTLED" in ln and "02/18/2026" in ln for ln in lines)
    notes = [r["note"] for r in rep["process_notes"]]
    assert any("log row reads" in n for n in notes) and any("unconfirmed" in n for n in notes)
    assert sum("NTD" in n for n in notes) == 3


# ---- K3 -------------------------------------------------------------------------
def _redate(corrected: str) -> str:
    f = {
        "severity": "MUST",
        "class": "1 dates",
        "anchor": "N1",
        "exhibit": 2,
        "page": 1,
        "problem": "the visit sheet reads 2/18/26, not 02/13/2026",
        "action": "redate",
        "corrected": corrected,
        "reason": "read off the page",
    }
    return json.dumps({"findings": [f], "complete": True})


def _review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reviews: list[str], finishes: list[str]
) -> tuple[int, SimpleNamespace, str]:
    """Collect re-dates the 2/18 visit onto the statement's 02/13 (the one
    statement date, a different visit); the review then corrects it."""
    stmt = "Sample Chiropractic\nPatient statement\nDate of service 02/13/2026 Spinal manipulation charges 65.00"
    sr = _matter(tmp_path, ["[handwritten]\nVisit sheet\nAdj C5 2/18/26", stmt])
    wrong, log = UC._settle_dates(sr, [_entry("02/18/2026")], [{"exhibit": 2, "id": "n2"}])  # type: ignore[arg-type]
    assert wrong[0].startswith("02/13/2026\n") and log[0]["verdict"] == "redated"
    later = (
        "02/15/2026\nSample Imaging | Medical Diagnoses\n\nMedical Diagnoses\n"
        "Lumbar radiographs showed no fracture and mild disc narrowing. (Exhibit 3 - p. 1)"
    )
    md = (
        "Alpha Example - Medical Chronology\n\n## Treatment Timeline\n\n## Diagnostic Highlights\n\n"
        f"## Medical Chronology\n\n{wrong[0]}\n\n{later}\n\n## Exhibit List\n"
    )
    out = sr.slug_dir / "out" / "alpha"
    out.mkdir(parents=True)
    for n in (2, 3):
        (out / f"Exhibit {n} - Sample.pdf").write_bytes(b"%PDF")
    ud = sr.slug_dir / "runs" / "alpha" / "update"
    (ud / "base.md").write_text(K.base_md())
    (ud / "new_exhibits.final.json").write_text(json.dumps([{"exhibit": 2, "id": "n2"}, {"exhibit": 3, "id": "n3"}]))
    doc = sr.slug_dir / "runs" / "alpha" / "final-chronology.md"
    doc.write_text(md)
    sr.doorway = K.ScriptedDoor(reviews, finishes)
    sr.cfg = SimpleNamespace(get=lambda *_a: {"review": "m"} if _a[-1] == "tiers" else sorted(K.HEADINGS))
    sr.log = sr.logged.append
    monkeypatch.setattr(UR.prompts, "load", lambda name, _cfg: name)
    monkeypatch.setattr(UR.audit_loop, "run", lambda _sr: 0)
    rc = UR.run(sr, images=lambda _e, _p: None, verify=lambda _a, _b: [], npages=lambda _x: 1)  # type: ignore[arg-type]
    return rc, sr, doc.read_text()


def test_the_review_corrects_a_wrong_redate_from_the_page_and_the_entry_moves_into_date_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean = json.dumps({"findings": [], "complete": True})
    rc, sr, md = _review(tmp_path, monkeypatch, [_redate("02/18/2026"), clean], [K.finish_reply(_redate("02/18/2026"))])
    _, ents = B.entries_of(md)
    assert rc == 0, sr.logged
    assert [B.iso(e) for e in ents] == ["2026-02-15", "2026-02-18"]  # moved after 02/15
    assert ents[1].startswith("02/18/2026\nSample Chiropractic")
    rec = json.loads((sr.slug_dir / "runs" / "alpha" / "update" / "review_repair.json").read_text())
    assert rec["status"] == "clean" and rec["open"] == []
    assert rec["rounds"][0]["findings"][0]["outcome"] == "applied"


def test_a_redate_the_cited_page_does_not_state_is_never_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rc, sr, md = _review(tmp_path, monkeypatch, [_redate("02/19/2026")], [K.finish_reply(_redate("02/19/2026"))])
    assert rc == 2  # held for SMD with the finding open, the date unchanged
    assert "02/19/2026" not in md and B.entries_of(md)[1][0].startswith("02/13/2026")
    rec = json.loads((sr.slug_dir / "runs" / "alpha" / "update" / "review_repair.json").read_text())
    assert rec["status"] == "held" and "does not state 02/19/2026" in rec["rounds"][0]["findings"][0]["outcome"]


# ---- K4 -------------------------------------------------------------------------
LEDGER = (
    "[handwritten]\nTREATMENT LOG\n1/3 C R\n12/10 C R\n1/7 C R\n1/14 C R\n1/4 C R\n1/28 C R\n2/4 C R\n"
    "7/18 C R\n7/25 C R\n"
)
LEDGER_STATEMENT = (
    "ITEMIZED STATEMENT\n"
    + "".join(
        f"| {d} | 98940 | Adjustment | 75.00 |\n"
        for d in (
            "12-03-25", "12-10-25", "01-07-26", "01-14-26", "01-21-26", "01-28-26", "02-04-26", "02-18-26", "02-25-26",
        )
    )
)  # fmt: skip


def test_the_ledgers_row_order_settles_each_misread_row_and_the_output_follows_it(tmp_path: Path) -> None:
    """Rows read "1/3", "1/4", "7/18", "7/25" are 12/3, 1/21, 2/18, 2/25: each
    sits between its neighbour rows, and the statement holds exactly that
    many unread dates there."""
    sr = _matter(tmp_path, [LEDGER_STATEMENT, LEDGER])
    readings = [
        "01/03/2026", "12/10/2025", "01/07/2026", "01/14/2026", "01/04/2026", "01/28/2026", "02/04/2026",
        "07/18/2026", "07/25/2026",
    ]  # fmt: skip
    got, log = UC._settle_dates(sr, [_entry(r, page=2) for r in readings], [{"exhibit": 2, "id": "n2"}])  # type: ignore[arg-type]
    assert [e[:10] for e in got] == [
        "12/03/2025", "12/10/2025", "01/07/2026", "01/14/2026", "01/21/2026", "01/28/2026", "02/04/2026",
        "02/18/2026", "02/25/2026",
    ]  # fmt: skip
    moved = {r["reading"]: r["date"] for r in log if r["verdict"] == "redated"}
    assert moved == {
        "01/03/2026": "12/03/2025", "01/04/2026": "01/21/2026", "07/18/2026": "02/18/2026", "07/25/2026": "02/25/2026",
    }  # fmt: skip
    assert all(r.get("by") == "ledger row order" for r in log)
