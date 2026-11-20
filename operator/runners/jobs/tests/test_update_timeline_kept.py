"""An update keeps each delivered Treatment Timeline row (2026-11-20: today's
alias order moved 3 delivered entries into another lane, and the lane was
counted in entries, not days, so a delivered 5 visits read 8). Through the
real assemble, the real grouping and the real audit document; every value is
invented."""

from __future__ import annotations

from pathlib import Path

import update_testkit as K
from jobs.medchron import config as config_mod
from jobs.medchron.update import assemble as A, collect as C
from jobs.stages.group import Canon
from jobs_testkit import FIRM_CONFIG

SYSTEM, CLINIC = "Example Health System", "Example Clinic"
#: The clinic's alias (the physician's surname) is listed before the system's,
#: as on the real firm's config: the first alias that matches wins.
ALIASES = [{"match": r"\bexample clinic\b|\bsmith\b", "label": CLINIC}, {"match": r"health system", "label": SYSTEM}]
ROWS = (
    f"| {SYSTEM} | 02/01/2026 - 02/03/2026 | 3 | Exhibit 1 - p. 1 |",
    f"| {CLINIC} | 02/01/2026 - 02/03/2026 | 3 | Exhibit 1 - p. 4 |",
)
NEX = [{"exhibit": 2, "title": "Exhibit 2 - Example Clinic (Medical Records)", "pages": 2}]


def _ent(d: str, provider: str, text: str, cite: str) -> str:
    return f"{d}\n{provider} | Medical Diagnoses\n\nMedical Diagnoses\n{text} {cite}"


def _base() -> str:
    # Each system visit shares its day with a clinic visit, and its provider line
    # names a health-system unit followed by the clinic's physician.
    ents = []
    for i, d in enumerate(("02/01/2026", "02/02/2026", "02/03/2026")):
        ents.append(
            _ent(d, f"{SYSTEM} Orthopedic Unit (Pat Smith, MD)", f"Unit visit {i}.", f"(Exhibit 1 - p. {i + 1})")
        )
        ents.append(_ent(d, CLINIC, f"Clinic visit {i}.", f"(Exhibit 1 - p. {i + 4})"))
    md = K.base_md(entries=tuple(ents))
    return md.replace("| Example Clinic | 12/01/2025 - 01/20/2026 | 2 | Exhibit 1 - p. 1 |", "\n".join(ROWS))


def _assemble(new: list[str]) -> tuple[str, dict]:
    data = {**FIRM_CONFIG, "providers": {**FIRM_CONFIG["providers"], "aliases": ALIASES}}
    canon = Canon(config_mod.FirmConfig(path=Path("<test>"), data=data))
    base = _base()
    return A.assemble(
        base,
        C.audit_document(base, new, NEX),
        describe=lambda c, o, t: (c, None, "x"),
        canon=canon,
        headings=K.HEADINGS,
        new_exhibits=NEX,
    )


def _timeline(md: str) -> list[str]:
    sec = md.split("## Treatment Timeline", 1)[1].split("## Diagnostic Highlights", 1)[0]
    return [ln for ln in sec.splitlines() if ln.startswith("| ") and "Medical Provider" not in ln]


def test_delivered_rows_stay_where_the_delivered_table_has_them() -> None:
    # A new entry of a third provider: neither delivered row changes.
    md, rep = _assemble([_ent("02/20/2026", "Example Imaging", "MRI of the neck.", "(Exhibit 2 - p. 1)")])
    rows = _timeline(md)
    assert rows[:2] == list(ROWS)
    assert rows[2] == "| Example Imaging | 02/20/2026 | 1 | Exhibit 2 - p. 1 |"
    # The delivered row that today's grouping would recount is still named for SMD.
    assert {d["lane"] for d in rep["delivered_table_vs_its_own_entries"]} == {SYSTEM, CLINIC}


def test_two_new_entries_on_one_day_add_one_visit() -> None:
    new = [
        _ent("02/10/2026", CLINIC, "Recheck of the neck.", "(Exhibit 2 - p. 1)"),
        _ent("02/10/2026", CLINIC, "Home exercises reviewed.", "(Exhibit 2 - p. 2)"),
    ]
    rows = _timeline(_assemble(new)[0])
    assert rows == [ROWS[0], f"| {CLINIC} | 02/01/2026 - 02/10/2026 | 4 | Exhibit 1 - p. 4 |"]
