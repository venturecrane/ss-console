"""C2 (2026-10-17): a collect-stage handwritten re-date is named to proof 10.

`update_collect._settle_dates` re-dates an entry cited to a handwritten page
(``handwritten.redate``), and the entry moves to its settled date. Proof 10
(``proofs.dropped_values``) reads every composed entry on its COMPOSED date,
so the moved entry's values (and the reading the re-date replaced) read as
lost and the update failed. The update review's redate names what it moved
(``review_repair.named_values``); the collect stage's did not. Now the
collect stage records the move, proof 10 looks for the entry's values on the
date it moved to, and only the reading the re-date itself replaced is named.
A value that is genuinely gone still fails. A yearless reading (compose wrote
a month and day only) is read under the year collect gave it.

Every value is invented."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from medchron.stages import update_collect as UC, update_finish as UF
from medchron.update import handwritten as H, proofs as P

PROV = "Example Chiropractic"
STATEMENT = "ITEMIZED STATEMENT\n" + "".join(
    f"| {d} | 98940 | Adjustment | 75.00 |\n" for d in ("01-07-26", "01-14-26", "01-21-26", "02-04-26")
)
LOG_PAGE = "[handwritten]\nTREATMENT LOG\n1/7 C R\n1/14 C R\n1/4 C R\n2/4 C R"
BODY = "Blood pressure 128/84, pulse 76."


def _entry(head: str, text: str = BODY) -> str:
    return f"{head}\n{PROV} | Treatment\n\nTreatment\n{text} (Exhibit 3 - p. 2)"


def _matter(tmp_path: Path) -> SimpleNamespace:
    sd = tmp_path / "matter"
    (sd / "text").mkdir(parents=True)
    pages = [STATEMENT, LOG_PAGE]
    (sd / "text" / "n3.txt").write_text(
        "".join(f"[p.{i}] (machine transcription)\n{t}\n" for i, t in enumerate(pages, 1))
    )
    ud = sd / "runs" / "alpha" / "update"
    ud.mkdir(parents=True)
    (ud / "update_plan.json").write_text(json.dumps({"exhibits": []}))
    return SimpleNamespace(slug_dir=sd, unit=SimpleNamespace(unit="alpha"))


def _settle(tmp_path: Path, composed: list[str]) -> tuple[list[str], dict]:
    got, log = UC._settle_dates(_matter(tmp_path), composed, [{"exhibit": 3, "id": "n3"}])  # type: ignore[arg-type]
    return got, {"handwritten_dates": log}


def _proof10(composed: list[str], updated: list[str], report: dict) -> list[tuple]:
    named, moves = H.moves_of(report)
    return P.dropped_values(composed, updated, {3}, named, moves)


READINGS = ["01/07/2026", "01/14/2026", "01/04/2026", "02/04/2026"]


def test_a_collect_redate_is_not_read_as_a_lost_value(tmp_path: Path) -> None:
    composed = [_entry(r) for r in READINGS]
    got, rep = _settle(tmp_path, composed)
    assert [e[:10] for e in got] == ["01/07/2026", "01/14/2026", "01/21/2026", "02/04/2026"]
    moved = [r["moved"] for r in rep["handwritten_dates"] if r.get("moved")]
    assert moved == [{"from": "2026-01-04", "to": "2026-01-21", "exhibits": [3], "removed": ["1/4/2026"]}]
    assert P.dropped_values(composed, got, {3}, set()) != []  # the defect: the move read as a loss
    assert _proof10(composed, got, rep) == []


def test_a_value_genuinely_lost_after_a_redate_still_fails_proof_10(tmp_path: Path) -> None:
    composed = [_entry(r) for r in READINGS]
    got, rep = _settle(tmp_path, composed)
    lossy = [e.replace(", pulse 76", "") if e.startswith("01/21/2026") else e for e in got]
    assert _proof10(composed, lossy, rep) == [("01/04/2026", ["76"])]


def test_a_redate_names_only_the_entry_it_moved(tmp_path: Path) -> None:
    """Another exhibit's entry on the composed date is not excused by the move."""
    composed = [_entry(r) for r in READINGS]
    got, rep = _settle(tmp_path, composed)
    other = "01/04/2026\nOther Clinic | Visit\n\nVisit\nWeight 180 lbs. (Exhibit 4 - p. 1)"
    updated = got + [other.replace("180", "18")]
    lost = P.dropped_values([*composed, other], updated, {3, 4}, *H.moves_of(rep))
    assert lost == [("01/04/2026", ["180"])]


def test_a_yearless_redate_is_read_under_the_year_collect_gave_it(tmp_path: Path) -> None:
    raw = [_entry("01/07/2026"), _entry("01/14/2026"), _entry("1/4 (date as written)"), _entry("02/04/2026")]
    comp, n = H.with_years(raw, 2026)
    # its neighbours put it in the next year (1/4 after 1/14); the ledger's row order moves it back
    assert n == 1 and comp[2].startswith("01/04/2027 " + H.NO_YEAR)
    got, rep = _settle(tmp_path, comp)
    assert got[2].startswith("01/21/2026") and H.NO_YEAR not in got[2]
    assert _proof10(comp, got, rep) == []


def test_finish_reads_composed_yearless_entries_and_moves_in_final_numbers(tmp_path: Path) -> None:
    raw = [_entry("01/07/2026"), _entry("01/14/2026"), _entry("1/4 (date as written)"), _entry("02/04/2026")]
    comp, _ = H.with_years(raw, 2026)
    got, rep = _settle(tmp_path, comp)
    ud, rd = tmp_path / "ud", tmp_path / "rd"
    ud.mkdir()
    rd.mkdir()
    (rd / "exhibit_map.json").write_text(json.dumps({"Exhibit 3 - Example Chiropractic records": 1}))
    (rd / "entries.md").write_text("\n\n".join(e.replace("Exhibit 3", "Exhibit 1") for e in raw) + "\n")
    (ud / "remap.json").write_text(json.dumps({"3": 5}))
    (ud / "new_exhibits.final.json").write_text(json.dumps([{"exhibit": 5, "composed_as": 3}]))
    (ud / "compose_report.json").write_text(json.dumps(rep))
    final = [e.replace("Exhibit 3", "Exhibit 5") for e in got]
    composed, new_ex = UF._composed(ud, rd, "2026-03-01")
    named, moves = UF._redates(ud)
    assert new_ex == {5} and moves == [("2027-01-04", "2026-01-21", {5})] and named == {"1/4/2027"}
    assert P.dropped_values(composed, final, new_ex, named, moves) == []
