"""A flowsheet's time rows are read by code, from the page and its neighbours
(2026-11-06, ``medchron/flowsheet_grid.py``).

A real update held because its drafter and its audit read one flowsheet
page's time rows as footers of the values above them; each is the header of
the values below it. The independent review was right, the audit reverted the
finisher's correct edit, and SMD corrected the sentence by hand. The cited
page alone fits both readings (every block five wide); its neighbours, whose
blocks run short, decide it. Every name, time and value here is synthetic,
shaped like the three real pages.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from jobs.medchron import flowsheet_grid as G
from jobs.medchron.audit import claims as CL
from jobs.medchron.update import review_repair as RR
from jobs.stages import update_review as UR, update_review_settle as URS
import update_testkit as K


def _t(*times: str) -> str:
    return "Row Name    " + "    ".join(times)


def _v(*vals: int) -> str:
    return "OTHER\nSample Early Warning Score    " + "    ".join(f"{v} -ZZ" for v in vals)


#: before the cited page: blocks of five, then a block of one (decides: header above)
P1 = "\n\n".join(
    ["Flowsheets - Other", _t("0800"), _v(5), _t("2345", "2330", "2315", "2300", "2245"), _v(3, 3, 3, 3, 3)]
    + [_t("1845", "1830", "1815", "1800", "1745"), _v(3, 3, 3, 3, 3), _t("1730", "1715", "1700", "1645", "1631")]
)
#: the cited page: every block five wide (fits both readings); the machine
#: transcription printed each score line ABOVE its time line
P2 = "\n".join(
    [
        _v(3, 3, 3, 3, 3),
        _t("1630", "1615", "1600", "1545", "1530"),
        _v(3, 3, 3, 3, 3),
        _t("1515", "1500", "1445", "1430", "1415"),
        _v(3, 3, 3, 3, 7),
        _t("1400", "1345", "1330", "1315", "1300"),
        _v(7, 7, 7, 7, 7),
        _t("1245", "1230", "1215", "1200", "1145"),
    ]
)
#: after it: five wide, then a block of one (decides: header above)
P3 = "\n".join([_v(7, 7, 7, 7, 7), _t("1130", "1115", "1100", "1045", "1030"), _v(7, 7, 7, 7, 7), _t("1015"), _v(7)])
PAGES = {1: P1, 2: P2, 3: P3}

#: the drafter's reading (time rows as footers) and the page's
WRONG = "The score was 7 at each entry from 1145 through 1300 and 3 at each entry from 1315 through 1630."
RIGHT = "The score was 7 at each entry from 1300 through 1415 and 3 at each entry from 1430 through 1630."
CITE = " (Exhibit 2 - p. 2)"


def _ev(pages: dict[int, str]) -> G.Evidence:
    return lambda ex, p: pages.get(p) if ex == 2 else None


# ---- the check -------------------------------------------------------------------------
def test_the_cited_page_fits_both_readings_and_its_neighbours_decide_header_above() -> None:
    assert G.vote(P2) is None and G.vote(P1) is True and G.vote(P3) is True
    assert G.orientation(P2, [P1, P3]) is True
    tab = dict(G.table(P2, True))
    assert tab["1415"] == "7" and tab["1430"] == "3" and "1145" not in tab  # 1145's values are on the next page


def test_the_drafters_footer_reading_is_corrected_to_the_pages_span() -> None:
    assert G.check(WRONG + CITE, _ev(PAGES), 2, 2) == RIGHT + CITE
    assert G.agrees(RIGHT + CITE, _ev(PAGES), 2, 2) and not G.agrees(WRONG + CITE, _ev(PAGES), 2, 2)
    assert G.check(RIGHT + CITE, _ev(PAGES), 2, 2) is None  # already the page's: nothing changes


#: the same cited page between neighbours whose short blocks print each time row
#: BELOW its values (footers): there the drafter's reading is the page's
F1 = "\n".join(
    [_v(5), _t("0800"), _v(3, 3, 3, 3, 3), _t("2345", "2330", "2315", "2300", "2245")]
    + [_v(3, 3, 3, 3, 3), _t("1845", "1830", "1815", "1800", "1745")]
)
F3 = "\n".join(
    [_v(7, 7, 7, 7, 7), _t("1130", "1115", "1100", "1045", "1030"), _v(7, 7, 7, 7, 7)]
    + [_t("1015", "1000", "0945", "0930", "0915"), _v(7), _t("0900")]
)


def test_neighbours_that_print_time_rows_below_their_values_read_the_page_as_footers() -> None:
    footers = {1: F1, 2: P2, 3: F3}
    assert G.vote(F1) is False and G.vote(F3) is False and G.reading(_ev(footers), 2, 2) is False
    assert G.check(RIGHT + CITE, _ev(footers), 2, 2) == WRONG + CITE
    assert G.check(WRONG + CITE, _ev(footers), 2, 2) is None


def test_falsifier_a_page_nothing_decides_changes_nothing() -> None:
    assert G.check(WRONG + CITE, _ev({2: P2}), 2, 2) is None  # no neighbours: undecided
    flipped = "\n".join(reversed(P1.split("\n")))  # a neighbour that votes the other way
    assert G.vote(flipped) is False
    assert G.check(WRONG + CITE, _ev({1: flipped, 2: P2, 3: P3}), 2, 2) is None
    plain = "The score was 7 at the last check. (Exhibit 2 - p. 2)"
    assert G.check(plain, _ev(PAGES), 2, 2) is None  # no stated span: nothing to set


# ---- in the update review ---------------------------------------------------------------
HEAD = "07/14/2026\nSample Medical Center | Additional findings\n\nAdditional findings\n"
LATER = (
    "09/10/2026\nSample Therapy | All Other Information\n\nAll Other Information\n"
    "Therapy evaluation recorded lumbar flexion limited to forty degrees. (Exhibit 3 - p. 1)"
)


def _scene(tmp_path: Path, sentence: str) -> tuple[SimpleNamespace, Path]:
    sd = tmp_path / "matter"
    ud = sd / "runs" / "alpha" / "update"
    ud.mkdir(parents=True)
    out = sd / "out" / "alpha"
    out.mkdir(parents=True)
    for n in (2, 3):
        (out / f"Exhibit {n} - Sample.pdf").write_bytes(b"%PDF")
    (ud / "base.md").write_text(K.base_md())
    (ud / "new_exhibits.final.json").write_text(json.dumps([{"exhibit": 2, "id": "doc-2"}]))
    (sd / "text").mkdir()
    body = "".join(f"[p.{n}] (machine transcription)\n{t}\n" for n, t in PAGES.items())
    (sd / "text" / "doc-2.txt").write_text(body)
    md = (
        "Alpha Example - Medical Chronology\n\n## Treatment Timeline\n\n## Diagnostic Highlights\n\n"
        f"## Medical Chronology\n\n{HEAD}{sentence}{CITE}\n\n{LATER}\n\n## Exhibit List\n"
    )
    doc = sd / "runs" / "alpha" / "final-chronology.md"
    doc.write_text(md)
    logged: list[str] = []
    sr = SimpleNamespace(
        slug_dir=sd,
        unit=SimpleNamespace(unit="alpha", client_name="Alpha Example", dob=None),
        job=SimpleNamespace(incident_date=K.INCIDENT, path=sd / "job" / "job.json"),
        cfg=SimpleNamespace(get=lambda *a: {"review": "m"} if a[-1] == "tiers" else sorted(K.HEADINGS)),
        log=logged.append,
        logged=logged,
    )
    return sr, doc


def _finding() -> RR.Finding:
    problem = "Each time row heads the score row printed beneath it, so the change falls after 1415"
    return RR.Finding("MUST", "1 wrong value", "N1.c1", 2, 2, problem, "replace", "", "")


def _rows(sr: SimpleNamespace) -> list[dict[str, Any]]:
    return CL.read_rows(sr.slug_dir / "out" / "alpha" / "audit-results.jsonl")


def test_a_held_finding_on_a_flowsheet_page_is_corrected_to_the_grid_and_nothing_holds(tmp_path: Path) -> None:
    sr, doc = _scene(tmp_path, WRONG)
    ud = sr.slug_dir / "runs" / "alpha" / "update"
    assert UR._hold(SimpleNamespace(sr=sr), {"rounds": []}, [_finding()], doc, ud) == 0
    md = doc.read_text()
    assert RIGHT + CITE in md and WRONG not in md and LATER in md
    rec = json.loads((ud / UR.RECORD).read_text())
    assert rec["status"] == "clean" and rec["open"] == []
    (grid,) = [r for r in rec["rounds"] if r.get("kind") == "flowsheet_grid"]
    assert grid["findings"][0]["outcome"] == "applied" and {"1145", "1315"} <= set(grid["findings"][0]["removed"])
    assert RR.named_values(rec) >= {"1145", "1315"}  # proof 10 reads them as named
    (row,) = _rows(sr)
    keys = {c["key"] for c in CL.extract_claims(CL.body_of(md), {2, 3}) if c["exhibit"] == 2}
    assert row["mode"] == "grid" and row["verdict"] == "SUPPORTED" and row["key"] in keys
    notes = URS.notes(sr.slug_dir / "out" / "alpha")
    assert "flowsheet whose time rows head the values printed below them" in notes[3]


def test_falsifier_a_sentence_already_the_pages_still_holds_on_its_finding(tmp_path: Path) -> None:
    sr, doc = _scene(tmp_path, RIGHT)
    before = doc.read_text()
    f = _finding()
    assert URS.settle(sr, doc, [f], {}) == [f]
    assert doc.read_text() == before and _rows(sr) == []


def test_the_finisher_keeps_a_sentence_the_grid_confirms_over_the_audits_revert(tmp_path: Path) -> None:
    sr, doc = _scene(tmp_path, RIGHT)
    md = doc.read_text()
    claims = [c for c in CL.extract_claims(CL.body_of(md), {2, 3}) if c["exhibit"] == 2]
    wrong = {"key": "k-wrong", "exhibit": 2, "page_spec": "2", "claim": WRONG}
    other = {"key": "k-other", "exhibit": 3, "page_spec": "1", "claim": "an unrelated sentence"}
    left = URS.grid_confirmed(sr, claims + [wrong, other], md, _ev(PAGES))
    assert left == [wrong, other]  # only the grid's own span is kept
    assert [r["key"] for r in _rows(sr)] == [claims[0]["key"]] and _rows(sr)[0]["mode"] == "grid"
