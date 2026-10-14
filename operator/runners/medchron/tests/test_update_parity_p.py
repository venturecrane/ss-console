"""Parity P: a yearless ledger row is an entry of its own, and a printout's
as-of date is never a visit.

On a real update (2026-10-14) compose wrote two handwritten ledger rows with
headings that carry no year ("07/18 (log date as written; year not stated)").
The entry splitter knew only MM/DD/YYYY, so both rows rode inside the entry
before them, an EMR problem list keyed to its as-of date, as body text. The
ledger's row order, which settled every other row of that page, never saw
them, and the firm would have read two visits with the wrong month inside an
unrelated entry. That entry was itself labelled a handwritten date though its
date is typed.

P1. A yearless heading is an entry; the ledger's row order dates it.
P2. One the row order cannot settle is reported open, never merged.
P3. A printout's as-of date is never a visit and never a handwritten reading.

Every value is invented; each test fails on the branch head before parity P.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import update_testkit as K
from medchron import prompts
from medchron.stages import update_collect as UC
from medchron.update import body as B

PROV = "Sample Chiropractic"
LEDGER = "[handwritten]\nTREATMENT LOG\n1/7 C R\n1/14 C R\n7/18 C R\n7/25 C R\n3/4 C R\n"
PRINTOUT = "Sample Health System\nProblem list as of 9/11/2026\nLow back pain, noted 01/07/2026"


def _statement(*dates: str) -> str:
    return "ITEMIZED STATEMENT\n" + "".join(f"| {d} | 98940 | Adjustment | 75.00 |\n" for d in dates)


def _row(head: str, page: int = 2) -> str:
    return f"{head}\n{PROV} | Treatments Administered During This Visit\n\nTreatments Administered During This Visit\nSpinal adjustment recorded on the log row. (Exhibit 1 - p. {page})"


AS_OF = (
    "09/11/2026 (problem list as-of date)\nSample Health System (provider not stated in the record) | "
    "HPI & Prior Medical History\n\nHPI & Prior Medical History\nThe problem list as of 9/11/2026 lists "
    "low back pain. (Exhibit 1 - p. 3)"
)


def _matter(tmp_path: Path, statement: str, composed: list[str]) -> SimpleNamespace:
    """A matter whose new document (Exhibit 2) is a statement, a handwritten
    ledger and a typed printout, and whose compose wrote `composed`."""
    sd = tmp_path / "matter"
    (sd / "text").mkdir(parents=True)
    pages = [statement, LEDGER, PRINTOUT]
    (sd / "text" / "n2.txt").write_text(
        "".join(f"[p.{i}] (machine transcription)\n{t}\n" for i, t in enumerate(pages, 1))
    )
    rd = sd / "runs" / "alpha"
    (rd / "update").mkdir(parents=True)
    (rd / "update" / "update_plan.json").write_text(json.dumps({"exhibits": []}))
    (rd / "exhibit_map.json").write_text(json.dumps({"Exhibit 2 - Sample Chiropractic.pdf": 1}))
    (rd / "entries.md").write_text("\n\n".join(composed) + "\n")
    return SimpleNamespace(
        slug_dir=sd,
        unit=SimpleNamespace(unit="alpha", client_name="Alpha Example", dob=None),
        job=SimpleNamespace(incident_date=K.INCIDENT, path=sd / "job" / "job.json"),
        cfg=None,
        logged=[],
    )


def _shape(sr: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> tuple[list[str], dict]:
    """The real collect shaping over the real composed entries (compose's own
    split), the keep decisions passed through."""
    monkeypatch.setattr(UC.C, "collect", lambda comp, _base, _inc, _canon: (sorted(comp, key=B.iso), {}))
    monkeypatch.setattr(UC, "Canon", lambda _cfg: None)
    rd = sr.slug_dir / "runs" / "alpha"
    return UC._shape(sr, K.base_md(), rd, [{"exhibit": 2, "id": "n2", "title": "Exhibit 2 - Sample"}])  # type: ignore[arg-type]


COMPOSED = [
    _row("01/07/2026"),
    _row("01/14/2026"),
    _row("03/04/2026"),
    AS_OF,
    _row("07/18 (log date as written; year not stated in the record)"),
    _row("07/25 (log date as written; year not stated in the record)"),
]


# ---- P1 -------------------------------------------------------------------------
def test_a_yearless_ledger_row_is_its_own_entry_and_the_row_order_dates_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sr = _matter(tmp_path, _statement("01-07-26", "01-14-26", "02-18-26", "02-25-26", "03-04-26"), COMPOSED)
    entries, rep = _shape(sr, monkeypatch)
    assert [e.partition("\n")[0][:10] for e in entries] == [
        "01/07/2026", "01/14/2026", "02/18/2026", "02/25/2026", "03/04/2026", "09/11/2026",
    ]  # fmt: skip
    for e in entries:
        assert "07/18" not in e and "07/25" not in e and "year not stated" not in e, e
    moved = {r["date"]: r.get("by") for r in rep["handwritten_dates"] if r["verdict"] == "redated"}
    assert moved == {"02/18/2026": "ledger row order", "02/25/2026": "ledger row order"}


# ---- P2 -------------------------------------------------------------------------
def test_a_yearless_row_the_row_order_cannot_settle_is_reported_open_never_merged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # One statement date between the 1/14 and 3/4 rows for two rows: nothing settles them.
    sr = _matter(tmp_path, _statement("01-07-26", "01-14-26", "02-18-26", "03-04-26"), COMPOSED)
    entries, rep = _shape(sr, monkeypatch)
    heads = [e.partition("\n")[0] for e in entries]
    opened = [h for h in heads if "year not stated in the record" in h]
    assert opened == [
        "07/18/2026 (handwritten date) (year not stated in the record)",
        "07/25/2026 (handwritten date) (year not stated in the record)",
    ]
    assert not any(e.startswith("02/18/2026") for e in entries)  # never decide's guess
    # Neither row rides inside another entry.
    for e in entries:
        assert e.count("Spinal adjustment recorded on the log row.") <= 1, e
    assert "7/18" not in next(e for e in entries if e.startswith("09/11/2026"))
    lines = UC.report_lines(rep)
    assert sum("NOT SETTLED" in ln and "states no year" in ln for ln in lines) == 2


# ---- P3 -------------------------------------------------------------------------
def test_a_printouts_as_of_date_is_never_a_visit_nor_a_handwritten_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The printout entry also cites the handwritten ledger page: its typed date is still no reading.
    as_of = AS_OF.replace("(Exhibit 1 - p. 3)", "(Exhibit 1 - p. 2-3)")
    sr = _matter(tmp_path, _statement("01-07-26", "01-14-26", "03-04-26"), [_row("01/07/2026"), as_of])
    entries, rep = _shape(sr, monkeypatch)
    printout = next(e for e in entries if e.startswith("09/11/2026"))
    assert not B.is_service(printout)
    assert "handwritten" not in printout.partition("\n")[0]
    assert not any(r.get("reading") == "09/11/2026" for r in rep["handwritten_dates"])
    # The map prompt says so at the source.
    text = (Path(prompts.__file__).parent / "map-system.md").read_text(encoding="utf-8")
    assert "as-of" in text and "no dated encounter on pp." in text
    assert "month and day with no year" in text
