"""Parity F: a handwritten treatment log in a "records and bills" file.

M1. The document's own typed itemized statement prints the doctor and the
street address, never the trade name the chronology uses, in a box-drawn
table with MM-DD-YY dates. No statement was found, so every visit read off
the handwritten log stayed unconfirmed and two misread dates went to the
firm as readings. The statement is the provider's because it is in the same
document; and a misread whose gap holds two free statement dates is never
moved onto the nearer one.

M2. The log records each visit as letter codes that a LEGEND on the page
defines; entries quoted the bare codes. Codes are written as what they mean,
and a code the legend defines ambiguously is put to the audit.

Every value is invented; each test fails on the code before this change.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

PROV = "Example Chiropractic"
STATEMENT = (
    "Page 1                                   02-20-26\n"
    "                ITEMIZED STATEMENT\n"
    "| Jordan Q. Sample, D.C.     | Patient: Pat Doe |\n"
    "| 100 Sample Way, Exampleton |                  |\n"
    "┌────┐\n"
    "│  Date    │ RVS/CPT │ Description        │ Charges │\n"
    + "".join(
        f"│ {d} │ 98940   │ Estab. Pt. Expanded │  75.00  │\n"
        for d in ("01-07-26", "01-14-26", "01-21-26", "01-28-26", "02-04-26")
    )
    + "| 02-11-26 | 97014 | Elec. Mus. Stim. | 40.00 |\n"
    "Balance due 02-20-26 400.00\n"
)
LOG_PAGE = "[handwritten]\nTREATMENT LOG\n1/7 C R\n1/14 C R\n1/4 C R\n1/18 C R\n2/4 C R\n2/11 C R"


def _entry(mdy: str, text: str = "Adjustment recorded on the log.", page: int = 2) -> str:
    return f"{mdy}\n{PROV} | Medical Diagnoses\n\nMedical Diagnoses\n{text} (Exhibit 3 - p. {page})"


def _matter(tmp_path: Path, pages: list[str]) -> SimpleNamespace:
    sd = tmp_path / "matter"
    (sd / "text").mkdir(parents=True)
    body = "".join(f"[p.{i}] (machine transcription)\n{t}\n" for i, t in enumerate(pages, 1))
    (sd / "text" / "n3.txt").write_text(body)
    ud = sd / "runs" / "alpha" / "update"
    ud.mkdir(parents=True)
    (ud / "update_plan.json").write_text(json.dumps({"exhibits": []}))
    return SimpleNamespace(slug_dir=sd, unit=SimpleNamespace(unit="alpha"))


# ---- M1 -------------------------------------------------------------------------
def test_a_logs_own_unnamed_itemized_statement_settles_its_handwritten_dates(tmp_path: Path) -> None:
    from medchron.stages import update_collect as UC

    sr = _matter(tmp_path, [STATEMENT, LOG_PAGE])
    readings = ["01/07/2026", "01/14/2026", "01/04/2026", "01/18/2026", "02/04/2026", "02/10/2026"]
    got, log = UC._settle_dates(sr, [_entry(r) for r in readings], [{"exhibit": 3, "id": "n3"}])  # type: ignore[arg-type]
    verdict = {r["reading"]: (r["verdict"], r.get("date")) for r in log}
    # The statement names no provider, yet it is the log's own document: its dates settle the readings.
    assert verdict["01/07/2026"] == ("confirmed", "01/07/2026")
    assert verdict["01/14/2026"] == ("confirmed", "01/14/2026")
    assert verdict["02/04/2026"] == ("confirmed", "02/04/2026")
    # One free statement date (02/11) and one unconfirmed reading in the gap after 02/04: re-dated.
    assert verdict["02/10/2026"] == ("redated", "02/11/2026")
    # 1/21 read as 01/04 and 1/28 read as 01/18: on the log the "1/4" row sits between the 1/14 and
    # 1/18 rows, so the LEDGER'S ROW ORDER settles both (parity K): the two rows between the confirmed
    # 1/14 and 2/4 rows take the two unread dates in order. Sorting the readings would have put 01/04
    # before 01/07 and moved 01/18 onto 01/21, the other visit's date.
    assert verdict["01/04/2026"] == ("redated", "01/21/2026") and verdict["01/18/2026"] == ("redated", "01/28/2026")
    assert [e[:10] for e in got] == ["01/07/2026", "01/14/2026", "01/21/2026", "01/28/2026", "02/04/2026", "02/11/2026"]
    # A statement's print and balance dates are never dates of service.
    from medchron.update import handwritten as H

    assert {d.isoformat() for d in H.service_dates([], PROV, [STATEMENT])} == {
        "2026-01-07", "2026-01-14", "2026-01-21", "2026-01-28", "2026-02-04", "2026-02-11",
    }  # fmt: skip


def test_the_audit_of_a_handwritten_log_page_is_given_its_own_documents_statement() -> None:
    from medchron.audit.handwritten_evidence import Evidence, statements_for
    from medchron.audit.run import Round

    ev = Evidence.__new__(Evidence)
    ev.body = _entry("01/18/2026", "Adjustment recorded on the log.")
    ev._pool = []  # no page anywhere names the provider
    ev.index = None
    doc = {1: STATEMENT, 2: LOG_PAGE}
    ev.page = lambda ex, p: doc.get(p) if ex == 3 else None  # type: ignore[method-assign]
    r = Round.__new__(Round)
    r._lock = threading.Lock()
    r.__dict__["hw_evidence"] = ev
    blocks = statements_for(r, {"exhibit": 3, "pages": [2], "claim": "Adjustment recorded on the log."})
    assert len(blocks) == 1 and "01-21-26" in blocks[0]["text"] and "ITEMIZED STATEMENT" in blocks[0]["text"]


def _d(mmdd: str):
    from datetime import date

    return date(2026, int(mmdd[:2]), int(mmdd[3:]))


def _settle(readings: list[str], statement: list[str]) -> dict[str, tuple[str, str | None]]:
    from medchron.update import handwritten as H

    rs = tuple(_d(r) for r in readings)
    dos = {_d(d) for d in statement}
    out = {}
    for r in rs:
        v, got = H.decide(r, dos, rs)
        out[f"{r.month:02d}/{r.day:02d}"] = (v, f"{got.month:02d}/{got.day:02d}" if got else None)
    return out


def test_two_misreads_between_confirmed_visits_take_the_two_unread_dates_in_order() -> None:
    """Confirmed 03/02 and 03/30 bracket two readings and two unread
    statement dates, 13 and 17 days off: no day window, the bracket decides."""
    got = _settle(["03/02", "03/04", "03/06", "03/30"], ["03/02", "03/17", "03/23", "03/30"])
    assert got["03/04"] == ("redated", "03/17") and got["03/06"] == ("redated", "03/23")
    assert got["03/02"] == ("confirmed", "03/02") and got["03/30"] == ("confirmed", "03/30")


def test_unequal_counts_between_confirmed_visits_stay_open() -> None:
    got = _settle(["03/02", "03/09", "03/30"], ["03/02", "03/16", "03/23", "03/30"])  # 1 reading, 2 dates
    assert got["03/09"] == ("open", None)
    got = _settle(["03/02", "03/09", "03/10", "03/30"], ["03/02", "03/16", "03/30"])  # 2 readings, 1 date
    assert got["03/09"] == ("open", None) and got["03/10"] == ("open", None)
    got = _settle(["03/02", "03/30"], ["03/02", "03/16", "03/30"])  # no reading: nothing to move
    assert set(got) == {"03/02", "03/30"}


def test_an_interval_at_the_statements_edge_settles_only_with_equal_counts_and_near_dates() -> None:
    # After the last confirmed visit: one reading, one unread date, within 7 days.
    assert _settle(["03/02", "04/02"], ["03/02", "04/06"])["04/02"] == ("redated", "04/06")
    # Open at one end, the 7-day window still stands in for the missing bracket.
    assert _settle(["03/02", "04/02"], ["03/02", "04/20"])["04/02"] == ("open", None)
    # Before the first confirmed visit: two readings, one unread date.
    got = _settle(["02/01", "02/03", "03/02"], ["02/10", "03/02"])
    assert got["02/01"] == ("open", None) and got["02/03"] == ("open", None)
    # After the last confirmed visit: readings past the statement's end, no unread date.
    assert _settle(["03/02", "05/01"], ["03/02"])["05/01"] == ("open", None)
    # No confirmed visit at all: equal counts at both edges still settle in order.
    got = _settle(["02/03", "02/05"], ["02/08", "02/10"])
    assert got == {"02/03": ("redated", "02/08"), "02/05": ("redated", "02/10")}


def test_two_entries_sharing_one_unconfirmed_reading_stay_open() -> None:
    got = _settle(["03/02", "03/09", "03/09", "03/30"], ["03/02", "03/16", "03/23", "03/30"])
    assert got["03/09"] == ("open", None)


# ---- M2 -------------------------------------------------------------------------
LEGEND_PAGE = (
    "[handwritten]\nLEGEND\nC - CERVICAL\nT - THORACIC\nR - REGULAR\nL - LIGHT\nO - OFF WORK\nL - LUMBAR\n"
    "SH - SOTO HALL\nK - [illegible]\n\n→ = SAME\n\nDATE | ADJUST | WORK STATUS | MUS STIM\n1/7  C T  R  [X]\n"
)
CONT_PAGE = "[handwritten]\nDATE | ADJUST | WORK STATUS | MUS STIM\n1/14  C  O  [X]\n"


def test_legend_codes_on_a_log_line_are_written_as_their_meanings() -> None:
    from medchron.update import legend as LG

    pages = {3: {1: STATEMENT, 2: LEGEND_PAGE, 3: CONT_PAGE}}
    entries = [
        _entry("01/07/2026", 'The log records an adjustment marked "C," work status "R" and one column checked.'),
        _entry("01/07/2026", "Adjustment levels C, T recorded with orthopedic test SH.", page=2),
        _entry("01/14/2026", 'The log records adjustment "C" and work status "O".', page=3),  # legend of p.2
        _entry("01/14/2026", 'The log records adjustment "C" and work status "L".', page=2),  # L is ambiguous
        _entry("01/14/2026", "Pain in the R and L shoulders was noted.", page=2),  # sides, never codes
    ]
    out, log = LG.expand(entries, pages)
    assert "marked cervical, work status regular and one column checked." in out[0]
    assert "Adjustment levels cervical, thoracic recorded" in out[1]
    assert "adjustment cervical and work status off work." in out[2]
    for e in out[:3]:
        assert not any(f'"{k}' in e for k in ("C", "R", "O")), e
    # The ambiguous code is never guessed: kept, logged, and put to the audit.
    assert 'work status "L"' in out[3] and "adjustment cervical" in out[3]
    assert {"cites": ["3:2"], "expanded": ["C"], "unclear": ["L"]} in log
    assert out[4] == entries[4]
    note = LG.audit_note('adjustment cervical and work status "L".', [LEGEND_PAGE])
    assert '"L"' in note and "legend on the page image" in note
    assert LG.audit_note("adjustment cervical and work status regular.", [LEGEND_PAGE]) == ""


def test_update_collect_writes_legend_codes_before_the_audit_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from medchron.stages import update_collect as UC

    sr = _matter(tmp_path, [STATEMENT, LEGEND_PAGE])
    sr.cfg = None
    sr.job = SimpleNamespace(incident_date="2025-12-01")
    composed = [_entry("01/07/2026", 'The log records an adjustment marked "C," work status "R".')]
    monkeypatch.setattr(UC.C, "composed", lambda rd: "")
    monkeypatch.setattr(UC.C, "collect", lambda *a: (list(composed), {"kept": 1}))
    monkeypatch.setattr(UC.B, "entries_of", lambda md: ("", []))
    monkeypatch.setattr(UC.B, "exhibit_titles", lambda md: {})
    monkeypatch.setattr(UC.P, "snap", lambda e, *a: (e, []))
    monkeypatch.setattr(UC, "Canon", lambda cfg: None)
    entries, rep = UC._shape(sr, "", tmp_path, [{"exhibit": 3, "id": "n3"}])  # type: ignore[arg-type]
    assert "marked cervical, work status regular." in entries[0]
    assert rep["legend_codes"] == [{"cites": ["3:2"], "expanded": ["C", "R"], "unclear": []}]


def test_the_compose_prompt_asks_for_a_legends_meanings_never_bare_codes() -> None:
    text = (Path(__file__).parents[1] / "medchron" / "prompts" / "map-system.md").read_text()
    assert "legend or key" in text and "never the bare code" in text
